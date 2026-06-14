"""
Process worker — claims pending laps and runs align + delta + PB management.

One or more replicas can run concurrently: SKIP LOCKED ensures each lap is
claimed by exactly one worker.  On startup, stale leases (worker crashed
mid-lap) are reset to pending so no lap is permanently stuck.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pandas as pd
import psycopg2.extras

from coach.align import align
from coach.config import cfg
from coach.db import conn
from coach.delta import compute_delta

logging.basicConfig(
    level=cfg.log_level,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("process")

POLL_INTERVAL = 3.0  # seconds between queue polls when idle


# ---------------------------------------------------------------------------
# DB helpers (process-specific)
# ---------------------------------------------------------------------------

def _release_stale_leases() -> None:
    c = conn()
    with c.cursor() as cur:
        cur.execute(
            """
            UPDATE laps SET status = 'pending', claimed_at = NULL
            WHERE status = 'processing'
              AND claimed_at < now() - INTERVAL '%s minutes'
            """,
            (cfg.worker_lease_minutes,),
        )
        n = cur.rowcount
    c.commit()
    if n:
        log.info("Reset %d stale lease(s) to pending", n)


def _claim_lap() -> dict | None:
    c = conn()
    with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            UPDATE laps
               SET status = 'processing',
                   claimed_at = now(),
                   attempts = attempts + 1
             WHERE id = (
                 SELECT l.id FROM laps l
                  WHERE l.status = 'pending'
                  ORDER BY l.created_at
                  FOR UPDATE SKIP LOCKED
                  LIMIT 1
             )
             RETURNING id, session_id, lap_index, lap_time, valid, lap_path
            """
        )
        row = cur.fetchone()
    c.commit()
    return dict(row) if row else None


def _get_session(session_id: int) -> dict:
    c = conn()
    with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT game, car, track FROM sessions WHERE id = %s", (session_id,))
        return dict(cur.fetchone())


def _mark_done(lap_id: str) -> None:
    c = conn()
    with c.cursor() as cur:
        cur.execute("UPDATE laps SET status = 'done' WHERE id = %s", (lap_id,))
    c.commit()


def _mark_failed(lap_id: str) -> None:
    c = conn()
    with c.cursor() as cur:
        cur.execute("UPDATE laps SET status = 'failed' WHERE id = %s", (lap_id,))
    c.commit()


def _get_pb(game: str, car: str, track: str) -> dict | None:
    c = conn()
    with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT lap_id, lap_time FROM pbs WHERE game=%s AND car=%s AND track=%s",
            (game, car, track),
        )
        row = cur.fetchone()
    return dict(row) if row else None


def _upsert_pb(game: str, car: str, track: str, lap_id: str, lap_time: float) -> None:
    c = conn()
    with c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pbs (game, car, track, lap_id, lap_time)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (game, car, track)
            DO UPDATE SET lap_id = EXCLUDED.lap_id, lap_time = EXCLUDED.lap_time
            """,
            (game, car, track, lap_id, lap_time),
        )
    c.commit()


def _insert_findings(lap_id: str, sectors: list[dict]) -> None:
    if not sectors:
        return
    max_loss = max(abs(s["time_loss_s"]) for s in sectors) or 1.0
    c = conn()
    with c.cursor() as cur:
        cur.execute("DELETE FROM findings WHERE lap_id = %s", (lap_id,))
        for s in sectors:
            severity = min(1.0, abs(s["time_loss_s"]) / max_loss)
            cur.execute(
                """
                INSERT INTO findings (lap_id, corner, kind, severity, time_loss_s, detail)
                VALUES (%s, %s, 'sector_delta', %s, %s, %s)
                """,
                (lap_id, s["sector"], severity, s["time_loss_s"],
                 psycopg2.extras.Json(s)),
            )
    c.commit()


# ---------------------------------------------------------------------------
# PB file helpers
# ---------------------------------------------------------------------------

def _pb_path(game: str, car: str, track: str) -> Path:
    return cfg.reference_dir / game / car / track / "pb.parquet"


def _load_pb_aligned(game: str, car: str, track: str) -> pd.DataFrame | None:
    p = _pb_path(game, car, track)
    return pd.read_parquet(p) if p.exists() else None


def _save_pb_aligned(game: str, car: str, track: str, aligned: pd.DataFrame) -> None:
    p = _pb_path(game, car, track)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".parquet.tmp")
    aligned.to_parquet(tmp, index=False)
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# Delta trace file helpers
# ---------------------------------------------------------------------------

def _findings_path(lap_id: str) -> Path:
    return cfg.findings_dir / f"{lap_id}_delta.parquet"


def _save_delta_trace(lap_id: str, trace: pd.DataFrame) -> None:
    p = _findings_path(lap_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".parquet.tmp")
    trace.to_parquet(tmp, index=False)
    os.replace(tmp, p)


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def _process_lap(lap: dict) -> None:
    lap_id: str = lap["id"]
    lap_time: float = lap["lap_time"]
    valid: bool = bool(lap["valid"])

    session = _get_session(lap["session_id"])
    game, car, track = session["game"], session["car"], session["track"]

    log.info("Processing %s  (lap %.3f s  valid=%s  %s/%s/%s)",
             lap_id, lap_time, valid, game, car, track)

    # Load and align
    raw_df = pd.read_parquet(lap["lap_path"])
    aligned = align(raw_df)

    pb_meta = _get_pb(game, car, track)
    pb_aligned = _load_pb_aligned(game, car, track)

    if pb_meta is None or pb_aligned is None:
        # First lap for this combo — register as PB and skip delta
        log.info("No PB found for %s/%s/%s — registering lap %s as PB", game, car, track, lap_id)
        if valid:
            _save_pb_aligned(game, car, track, aligned)
            _upsert_pb(game, car, track, lap_id, lap_time)
        _mark_done(lap_id)
        return

    # Compute delta vs PB
    trace, sectors = compute_delta(aligned, pb_aligned)
    _save_delta_trace(lap_id, trace)
    _insert_findings(lap_id, sectors)

    total_delta = float(trace["delta"].iloc[-1])
    log.info("Delta vs PB: %+.3f s  (worst sector: %.3f s)",
             total_delta,
             sectors[0]["time_loss_s"] if sectors else 0.0)

    # Update PB if this lap is faster and valid
    if valid and lap_time < pb_meta["lap_time"]:
        log.info("New PB: %.3f s (was %.3f s)", lap_time, pb_meta["lap_time"])
        _save_pb_aligned(game, car, track, aligned)
        _upsert_pb(game, car, track, lap_id, lap_time)

    _mark_done(lap_id)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> None:
    log.info("Process worker started (PID %d)", os.getpid())
    cfg.findings_dir.mkdir(parents=True, exist_ok=True)
    cfg.reference_dir.mkdir(parents=True, exist_ok=True)

    _release_stale_leases()

    while True:
        lap = _claim_lap()
        if lap is None:
            time.sleep(POLL_INTERVAL)
            continue

        try:
            _process_lap(lap)
        except Exception:
            log.exception("Failed processing lap %s", lap.get("id"))
            try:
                _mark_failed(lap["id"])
            except Exception:
                pass


if __name__ == "__main__":
    run()
