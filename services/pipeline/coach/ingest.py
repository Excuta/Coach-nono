"""
Ingest worker — runs inside Docker, watches data/raw/ for completed laps.

Polls raw/ every 2 s for *.meta.json files written by the capture agent.
For each new file:
  1. Validates the parquet (readable, non-empty).
  2. Inserts a sessions row (upsert on capture_id) and a laps row (status=pending).
  3. Moves the parquet to laps/<session_id>/ and deletes the meta.json.

Idempotent: ON CONFLICT DO NOTHING on laps; re-runs after a crash are safe.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

import pandas as pd

from coach.config import cfg
from coach.db import get_or_create_session, insert_lap, lap_id as make_lap_id

logging.basicConfig(
    level=cfg.log_level,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ingest")

POLL_INTERVAL = 2.0   # seconds between raw/ scans
MIN_SAMPLES = 100     # reject obviously empty/corrupt parquets


def _process(meta_path: Path) -> None:
    try:
        meta = json.loads(meta_path.read_text())
    except Exception as exc:
        log.error("Cannot parse %s: %s — skipping", meta_path, exc)
        meta_path.unlink(missing_ok=True)
        return

    capture_id: str = meta["session_id"]
    lap_index: int = meta["lap_index"]
    parquet_src = meta_path.parent / meta["parquet_file"]

    dest_dir = cfg.laps_dir / capture_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / meta["parquet_file"]

    # --- Move parquet (handle already-moved case) ---
    if parquet_src.exists():
        shutil.move(str(parquet_src), str(dest_path))
    elif not dest_path.exists():
        log.error("Parquet missing from both raw and laps: %s", meta["parquet_file"])
        meta_path.unlink(missing_ok=True)
        return

    # --- Validate ---
    try:
        df = pd.read_parquet(dest_path)
    except Exception as exc:
        log.error("Cannot read parquet %s: %s", dest_path, exc)
        meta_path.unlink(missing_ok=True)
        return

    if len(df) < MIN_SAMPLES:
        log.warning("Lap %d has only %d samples — registered but may be partial", lap_index, len(df))

    # --- Register in DB ---
    db_session_id = get_or_create_session(
        capture_id=capture_id,
        game=meta["game"],
        car=meta["car"],
        track=meta["track"],
        session_type=meta["session_type"],
        conditions=meta.get("conditions", {}),
    )

    lid = make_lap_id(capture_id, lap_index)
    insert_lap(
        lid=lid,
        session_db_id=db_session_id,
        lap_index=lap_index,
        lap_time_s=meta["lap_time_ms"] / 1000.0,
        valid=bool(meta.get("valid", True)),
        raw_path=str(parquet_src),
        lap_path=str(dest_path),
    )

    meta_path.unlink()

    lap_s = meta["lap_time_ms"] / 1000.0
    m, s = divmod(lap_s, 60)
    log.info(
        "Ingested %s  idx=%d  %d:%06.3f  valid=%s  samples=%d",
        lid, lap_index, int(m), s,
        meta.get("valid", True),
        meta.get("sample_count", len(df)),
    )


def run() -> None:
    log.info("Ingest worker started. Watching %s", cfg.raw_dir)
    cfg.raw_dir.mkdir(parents=True, exist_ok=True)
    cfg.laps_dir.mkdir(parents=True, exist_ok=True)

    while True:
        for meta_path in sorted(cfg.raw_dir.rglob("*.meta.json")):
            try:
                _process(meta_path)
            except Exception as exc:
                log.exception("Unexpected error processing %s: %s", meta_path, exc)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
