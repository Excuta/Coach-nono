from __future__ import annotations

import hashlib
import json
import logging

import psycopg2
import psycopg2.extras

from coach.config import cfg

log = logging.getLogger(__name__)

_conn: psycopg2.extensions.connection | None = None


def conn() -> psycopg2.extensions.connection:
    global _conn
    if _conn is None or _conn.closed:
        _conn = psycopg2.connect(cfg.database_url)
        _conn.autocommit = False
    elif _conn.get_transaction_status() == psycopg2.extensions.TRANSACTION_STATUS_INERROR:
        # Recover from an aborted transaction left by a prior failure.
        _conn.rollback()
    return _conn


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def get_or_create_session(
    capture_id: str,
    game: str,
    car: str,
    track: str,
    session_type: str,
    conditions: dict,
    statics: dict | None = None,
) -> int:
    st = statics or {}
    c = conn()
    with c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO sessions
              (capture_id, game, car, track, session_type, conditions,
               max_rpm, max_fuel, sector_count, player_name, is_online, statics)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (capture_id) DO UPDATE SET
                max_rpm      = COALESCE(EXCLUDED.max_rpm,      sessions.max_rpm),
                max_fuel     = COALESCE(EXCLUDED.max_fuel,     sessions.max_fuel),
                sector_count = COALESCE(EXCLUDED.sector_count, sessions.sector_count),
                player_name  = COALESCE(EXCLUDED.player_name,  sessions.player_name),
                is_online    = COALESCE(EXCLUDED.is_online,    sessions.is_online),
                statics      = COALESCE(EXCLUDED.statics,      sessions.statics)
            RETURNING id
            """,
            (
                capture_id, game, car, track, session_type,
                psycopg2.extras.Json(conditions),
                st.get("max_rpm") or None,
                st.get("max_fuel") or None,
                st.get("sector_count") or None,
                st.get("player_name") or None,
                st.get("is_online"),
                psycopg2.extras.Json(st) if st else None,
            ),
        )
        row = cur.fetchone()
    c.commit()
    return row[0]


# ---------------------------------------------------------------------------
# Lap helpers
# ---------------------------------------------------------------------------

def lap_id(capture_id: str, lap_index: int) -> str:
    """Stable, deterministic lap identifier."""
    raw = f"{capture_id}:{lap_index}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def insert_lap(
    lid: str,
    session_db_id: int,
    lap_index: int,
    lap_time_s: float,
    valid: bool,
    raw_path: str,
    lap_path: str,
) -> None:
    c = conn()
    with c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO laps
              (id, session_id, lap_index, lap_time, valid, raw_path, lap_path, status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending')
            ON CONFLICT (id) DO NOTHING
            """,
            (lid, session_db_id, lap_index, lap_time_s, valid, raw_path, lap_path),
        )
    c.commit()
    log.debug("Lap %s inserted (session=%d idx=%d)", lid, session_db_id, lap_index)


# ---------------------------------------------------------------------------
# Extras + coordinates helpers
# ---------------------------------------------------------------------------

def insert_extras(lap_id: str, agg: dict) -> None:
    if not agg:
        return
    c = conn()
    cols = list(agg.keys())
    placeholders = ", ".join(["%s"] * len(cols))
    sql = (
        f"INSERT INTO extras (lap_id, {', '.join(cols)}) "
        f"VALUES (%s, {placeholders}) "
        f"ON CONFLICT (lap_id) DO NOTHING"
    )
    with c.cursor() as cur:
        cur.execute(sql, [lap_id] + list(agg.values()))
    c.commit()


def insert_coords(lap_id: str, coords_path: str, sample_count: int) -> None:
    c = conn()
    with c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO coordinates (lap_id, coords_path, sample_count)
            VALUES (%s, %s, %s)
            ON CONFLICT (lap_id) DO NOTHING
            """,
            (lap_id, coords_path, sample_count),
        )
    c.commit()
