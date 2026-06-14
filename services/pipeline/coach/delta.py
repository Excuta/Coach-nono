"""
Delta computation: cumulative time-delta + per-mini-sector time loss.

Inputs are two aligned DataFrames on the same GRID_N spline grid (from align.py).
Delta is positive when the current lap is SLOWER than the reference.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

N_SECTORS = 20  # mini-sectors dividing the lap equally by spline


def compute_delta(
    current: pd.DataFrame,
    reference: pd.DataFrame,
) -> tuple[pd.DataFrame, list[dict]]:
    """
    Compare current lap to reference (PB) lap.

    Returns
    -------
    trace : pd.DataFrame
        GRID_N rows with columns [spline, delta, speed, throttle, brake, steer].
        delta > 0 means slower than reference at this point.
    sectors : list[dict]
        N_SECTORS dicts, each with sector metadata and time_loss_s.
        Sorted worst (most time lost) first.
    """
    delta = current["lap_elapsed"].to_numpy() - reference["lap_elapsed"].to_numpy()

    trace = pd.DataFrame({
        "spline":   current["spline"].to_numpy(),
        "delta":    delta,
        "speed":    current.get("speed",    pd.Series(dtype=float)).to_numpy(),
        "throttle": current.get("throttle", pd.Series(dtype=float)).to_numpy(),
        "brake":    current.get("brake",    pd.Series(dtype=float)).to_numpy(),
        "steer":    current.get("steer",    pd.Series(dtype=float)).to_numpy(),
    })

    sectors = _sector_losses(delta, current["spline"].to_numpy())
    return trace, sectors


def _sector_losses(delta: np.ndarray, spline: np.ndarray) -> list[dict]:
    n = len(delta)
    edges = np.linspace(0.0, 1.0, N_SECTORS + 1)
    sectors = []

    for i in range(N_SECTORS):
        s0, s1 = edges[i], edges[i + 1]
        # Indices within this sector
        in_sector = np.where((spline >= s0) & (spline < s1))[0]
        if len(in_sector) < 2:
            continue
        time_loss = float(delta[in_sector[-1]] - delta[in_sector[0]])
        sectors.append({
            "sector":       i,
            "spline_start": round(float(s0), 4),
            "spline_end":   round(float(s1), 4),
            "time_loss_s":  round(time_loss, 4),
        })

    sectors.sort(key=lambda x: x["time_loss_s"], reverse=True)
    return sectors
