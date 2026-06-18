"""DB operations for the corner analysis layer (corners, corner_stats, corner_baselines)."""
from __future__ import annotations

import logging

import numpy as np
import psycopg2.extras

from .corners import Corner, MIN_CORNER_LAPS

log = logging.getLogger(__name__)

MIN_BASELINE_SAMPLES = 5  # baselines flagged as "learning" below this

BASELINE_METRICS = [
    "entry_speed_kph",
    "apex_speed_kph",
    "exit_speed_kph",
    "brake_point",
    "throttle_point",
    "coast_duration",
    "trail_brake_overlap",
    "max_lat_g",
    "min_slip_ratio",
    "steer_reversals",
]


# ---------------------------------------------------------------------------
# Corner geometry
# ---------------------------------------------------------------------------

def get_corners(conn, game: str, track: str) -> list[dict]:
    """Return stored corner rows for game+track, ordered by corner_index."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM corners WHERE game=%s AND track=%s ORDER BY corner_index",
            (game, track),
        )
        return [dict(r) for r in cur.fetchall()]


def store_corners(conn, game: str, track: str, corners: list[Corner]) -> list[dict]:
    """Upsert detected corner geometry and return the stored rows."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        for c in corners:
            cur.execute(
                """
                INSERT INTO corners
                  (game, track, corner_index, name, spline_start, spline_apex, spline_end)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (game, track, corner_index) DO UPDATE SET
                  name=EXCLUDED.name,
                  spline_start=EXCLUDED.spline_start,
                  spline_apex=EXCLUDED.spline_apex,
                  spline_end=EXCLUDED.spline_end
                """,
                (game, track, c.index, c.name, c.spline_start, c.spline_apex, c.spline_end),
            )
        conn.commit()
        cur.execute(
            "SELECT * FROM corners WHERE game=%s AND track=%s ORDER BY corner_index",
            (game, track),
        )
        return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Lap readiness checks
# ---------------------------------------------------------------------------

def count_done_laps(conn, game: str, track: str) -> int:
    """Count valid done laps for this game+track (used to decide if detection is ready)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT COUNT(*) FROM laps l
            JOIN sessions s ON s.id = l.session_id
            WHERE s.game=%s AND s.track=%s AND l.status='done' AND l.valid=true
            """,
            (game, track),
        )
        return int(cur.fetchone()[0])


def get_fast_lap_paths(conn, game: str, track: str, n: int = MIN_CORNER_LAPS) -> list[str]:
    """Return lap_path for the N fastest valid done laps (for corner speed trace averaging)."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT l.lap_path FROM laps l
            JOIN sessions s ON s.id = l.session_id
            WHERE s.game=%s AND s.track=%s AND l.status='done' AND l.valid=true
              AND l.lap_path IS NOT NULL
            ORDER BY l.lap_time
            LIMIT %s
            """,
            (game, track, n),
        )
        return [r[0] for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Per-lap corner stats
# ---------------------------------------------------------------------------

def store_corner_stats(
    conn,
    lap_id: str,
    game_version_major: str,
    corner_rows: list[dict],
    corner_id_map: dict[int, int],
) -> None:
    """Replace corner_stats rows for a lap."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM corner_stats WHERE lap_id=%s", (lap_id,))
        for row in corner_rows:
            cid = corner_id_map.get(row["corner_index"])
            if cid is None:
                continue
            cur.execute(
                """
                INSERT INTO corner_stats
                  (lap_id, corner_id, game_version_major,
                   entry_speed_kph, apex_speed_kph, exit_speed_kph,
                   brake_point, throttle_point, coast_duration,
                   trail_brake_overlap, max_lat_g, min_slip_ratio, steer_reversals)
                VALUES (%s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s,%s)
                ON CONFLICT (lap_id, corner_id) DO NOTHING
                """,
                (
                    lap_id, cid, game_version_major,
                    row["entry_speed_kph"], row["apex_speed_kph"], row["exit_speed_kph"],
                    row["brake_point"], row["throttle_point"], row["coast_duration"],
                    row["trail_brake_overlap"], row["max_lat_g"], row["min_slip_ratio"],
                    row["steer_reversals"],
                ),
            )
    conn.commit()


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def update_baselines(
    conn,
    game: str,
    game_version_major: str,
    track: str,
    car: str,
    corner_id: int,
) -> None:
    """Recompute percentile baselines for all metrics of one corner."""
    with conn.cursor() as cur:
        for metric in BASELINE_METRICS:
            cur.execute(
                f"""
                SELECT cs.{metric}
                FROM corner_stats cs
                JOIN laps l ON l.id = cs.lap_id
                JOIN sessions s ON s.id = l.session_id
                WHERE cs.corner_id = %s
                  AND s.game = %s AND cs.game_version_major = %s
                  AND s.track = %s AND s.car = %s
                  AND cs.{metric} IS NOT NULL AND l.valid = true
                """,
                (corner_id, game, game_version_major, track, car),
            )
            rows = cur.fetchall()
            if not rows:
                continue
            vals = np.array([r[0] for r in rows], dtype=float)
            n = len(vals)
            cur.execute(
                """
                INSERT INTO corner_baselines
                  (game, game_version_major, track, car, corner_id, metric,
                   p10, p25, p50, p75, p90, stddev, sample_count, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,%s,%s,%s, now())
                ON CONFLICT (game, game_version_major, track, car, corner_id, metric)
                DO UPDATE SET
                  p10=EXCLUDED.p10, p25=EXCLUDED.p25, p50=EXCLUDED.p50,
                  p75=EXCLUDED.p75, p90=EXCLUDED.p90,
                  stddev=EXCLUDED.stddev, sample_count=EXCLUDED.sample_count,
                  updated_at=EXCLUDED.updated_at
                """,
                (
                    game, game_version_major, track, car, corner_id, metric,
                    float(np.percentile(vals, 10)), float(np.percentile(vals, 25)),
                    float(np.percentile(vals, 50)), float(np.percentile(vals, 75)),
                    float(np.percentile(vals, 90)), float(np.std(vals)), n,
                ),
            )
    conn.commit()


def get_baselines(
    conn,
    game: str,
    game_version_major: str,
    track: str,
    car: str,
    corner_id: int,
) -> dict[str, dict]:
    """Return metric -> {p10, p25, p50, p75, p90, stddev, sample_count} for a corner."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT metric, p10, p25, p50, p75, p90, stddev, sample_count
            FROM corner_baselines
            WHERE game=%s AND game_version_major=%s AND track=%s AND car=%s AND corner_id=%s
            """,
            (game, game_version_major, track, car, corner_id),
        )
        return {
            row[0]: {
                "p10": row[1], "p25": row[2], "p50": row[3],
                "p75": row[4], "p90": row[5], "stddev": row[6],
                "sample_count": row[7],
            }
            for row in cur.fetchall()
        }
