"""
Lap alignment: resample a raw lap DataFrame onto a uniform spline grid.

A single parquet may contain data from multiple lap crossings (e.g. when the
capture agent starts mid-session).  _extract_flying_lap() isolates the segment
that corresponds to the actual completed lap before interpolating.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d

GRID_N = 1000  # grid points across the lap (spline 0→1)

_INTERP_COLS = ["lap_elapsed", "speed", "throttle", "brake", "steer", "gear", "rpm", "fuel"]


def align(df: pd.DataFrame) -> pd.DataFrame:
    """Return a GRID_N-row DataFrame resampled on a uniform spline grid."""
    seg = _extract_flying_lap(df)
    seg = seg.copy()
    seg["lap_elapsed"] = (seg["t"] - seg["t"].min()).clip(lower=0.0)

    # Sort by spline; collapse duplicate positions by keeping the first occurrence
    seg = seg.sort_values("spline").drop_duplicates(subset="spline", keep="first")

    grid = np.linspace(0.0, 1.0, GRID_N)
    out: dict[str, np.ndarray] = {"spline": grid}

    for col in _INTERP_COLS:
        if col not in seg.columns or seg[col].isna().all():
            continue
        s, v = seg["spline"].to_numpy(), seg[col].to_numpy()
        f = interp1d(s, v, kind="linear", bounds_error=False,
                     fill_value=(float(v[0]), float(v[-1])))
        out[col] = f(grid)

    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_flying_lap(df: pd.DataFrame) -> pd.DataFrame:
    """
    Isolate the flying-lap segment from a raw parquet buffer.

    A buffer may contain:
      • 0 crossings — entire buffer is the lap (unusual edge case)
      • 1 crossing  — data is everything up to the crossing (normal)
      • 2+ crossings — flying lap is between the last two crossings

    Crossings are detected as drops of >50 s in the lap_time column.
    """
    lt = df["lap_time"]
    drop_mask = lt.diff() < -50.0
    crossing_positions = df.index[drop_mask].tolist()

    if len(crossing_positions) == 0:
        return df

    lap_end = crossing_positions[-1]

    if len(crossing_positions) >= 2:
        lap_start = crossing_positions[-2]
    else:
        lap_start = df.index[0]

    # Use iloc exclusive-end so the crossing sample at lap_end (which has
    # spline≈0 in some ACC versions) is never included in the flying-lap
    # segment — if it were included it would become the idxmin target and
    # collapse the entire segment to 1 row.
    seg = df.iloc[lap_start:lap_end].copy()

    # Start from the minimum spline position — handles the case where the
    # segment begins at spline≈1.0 right after a crossing and needs to
    # "roll over" to 0 before the flying lap data begins.
    seg = seg.loc[seg["spline"].idxmin():]

    # Remove the wrap-around tail (last few samples where spline drops back
    # toward 0 after crossing the finish line at the end of the flying lap).
    seg = seg[seg["spline"] <= seg["spline"].cummax()]
    return seg
