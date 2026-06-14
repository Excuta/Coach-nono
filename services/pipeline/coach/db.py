from __future__ import annotations

import hashlib
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
) -> int:
    c = conn()
    with c.cursor() as cur:
        # Upsert: DO UPDATE with no real change so RETURNING always fires
        cur.execute(
            """
            INSERT INTO sessions (capture_id, game, car, track, session_type, conditions)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (capture_id)
            DO UPDATE SET capture_id = EXCLUDED.capture_id
            RETURNING id
            """,
            (capture_id, game, car, track, session_type,
             psycopg2.extras.Json(conditions)),
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
