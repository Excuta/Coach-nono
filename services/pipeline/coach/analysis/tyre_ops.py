"""Per-lap tyre and brake aggregate computation from a lap DataFrame."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_WHEELS = ("fl", "fr", "rl", "rr")


def _wheel_agg(series: pd.Series, agg: str) -> list[float | None]:
    """Return per-wheel aggregate from a column of 4-element arrays."""
    try:
        arr = np.stack(series.dropna().to_numpy())  # (N, 4)
        if agg == "mean":
            vals = arr.mean(axis=0)
        elif agg == "max":
            vals = arr.max(axis=0)
        elif agg == "last":
            vals = arr[-1]
        else:
            raise ValueError(agg)
        return [round(float(v), 4) for v in vals]
    except Exception:
        return [None, None, None, None]


def compute_tyre_stats(df: pd.DataFrame) -> dict:
    """Compute per-wheel tyre/brake aggregates from a lap DataFrame.

    Handles missing columns gracefully — any column absent in df is skipped.
    Returns a flat dict keyed by <metric>_<wheel> suitable for tyre_laps insert.
    """
    stats: dict = {}

    if "tyre_press" in df.columns:
        for w, v in zip(_WHEELS, _wheel_agg(df["tyre_press"], "mean")):
            stats[f"press_avg_{w}"] = v

    if "tyre_temp" in df.columns:
        for w, v in zip(_WHEELS, _wheel_agg(df["tyre_temp"], "mean")):
            stats[f"temp_avg_{w}"] = v
        for w, v in zip(_WHEELS, _wheel_agg(df["tyre_temp"], "max")):
            stats[f"temp_max_{w}"] = v

    if "pad_life" in df.columns:
        for w, v in zip(_WHEELS, _wheel_agg(df["pad_life"], "last")):
            stats[f"pad_life_{w}"] = v

    if "disc_life" in df.columns:
        for w, v in zip(_WHEELS, _wheel_agg(df["disc_life"], "last")):
            stats[f"disc_life_{w}"] = v

    return stats


def store_tyre_stats(conn, lap_id: str, stats: dict) -> None:
    if not stats:
        return
    cols = list(stats.keys())
    vals = [stats[c] for c in cols]
    col_str = ", ".join(cols)
    ph = ", ".join(["%s"] * len(vals))
    upsert = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO tyre_laps (lap_id, {col_str}) VALUES (%s, {ph})"
            f" ON CONFLICT (lap_id) DO UPDATE SET {upsert}",
            [lap_id] + vals,
        )
    conn.commit()
