"""Corner detection and per-corner metric extraction."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.signal import find_peaks, savgol_filter

log = logging.getLogger(__name__)

GRID_N = 1000
MIN_CORNER_LAPS = 3   # laps needed before attempting corner detection
_ENTRY_EXIT_FRAC = 0.2  # first/last 20% of corner window = entry/exit zone
_APEX_EXPAND_KPH = 15.0  # km/h above apex speed = corner boundary
_PEAK_DISTANCE = 30      # min grid points between apices (~3% of lap)
_PEAK_PROMINENCE = 8.0   # min km/h drop to count as a corner


@dataclass
class Corner:
    index: int
    name: str
    spline_start: float
    spline_apex: float
    spline_end: float


def detect_corners(speed_traces: list[np.ndarray]) -> list[Corner]:
    """Auto-detect corners by finding speed minima in averaged aligned traces.

    speed_traces: list of 1-D arrays of shape (GRID_N,) in m/s.
    Returns corners ordered by spline position.
    """
    avg_kph = np.mean(np.stack(speed_traces), axis=0) * 3.6

    # Savitzky-Golay smoothing (needs odd window, at least 5)
    window = min(21, (len(avg_kph) // 20) * 2 + 1)
    window = max(window, 5)
    smooth = savgol_filter(avg_kph, window_length=window, polyorder=2)

    peaks, _ = find_peaks(
        -smooth,
        prominence=_PEAK_PROMINENCE,
        distance=_PEAK_DISTANCE,
    )

    corners: list[Corner] = []
    for i, apex_idx in enumerate(peaks):
        apex_kph = smooth[apex_idx]
        threshold = apex_kph + _APEX_EXPAND_KPH

        # Walk left to find corner entry
        start_idx = max(0, apex_idx - 1)
        for j in range(apex_idx - 1, max(0, apex_idx - 200), -1):
            if smooth[j] >= threshold:
                start_idx = j
                break

        # Walk right to find corner exit
        end_idx = min(GRID_N - 1, apex_idx + 1)
        for j in range(apex_idx + 1, min(GRID_N, apex_idx + 200)):
            if smooth[j] >= threshold:
                end_idx = j
                break

        corners.append(Corner(
            index=i + 1,
            name=f"T{i + 1}",
            spline_start=round(float(start_idx) / GRID_N, 4),
            spline_apex=round(float(apex_idx) / GRID_N, 4),
            spline_end=round(float(end_idx) / GRID_N, 4),
        ))

    log.info("Detected %d corners", len(corners))
    return corners


def extract_corner_stats(df: pd.DataFrame, corners: list[Corner]) -> list[dict]:
    """Extract per-corner driving metrics from an aligned lap DataFrame.

    df: aligned 1000-row DataFrame with at least spline, speed, throttle, brake columns.
    Returns one dict per corner with numeric metrics.
    """
    spline = df["spline"].values
    speed_kph = df["speed"].values * 3.6
    throttle = df["throttle"].values if "throttle" in df.columns else np.zeros(len(spline))
    brake = df["brake"].values if "brake" in df.columns else np.zeros(len(spline))
    steer = df["steer"].values if "steer" in df.columns else None
    g_lat = df["g_lat"].values if "g_lat" in df.columns else None

    slip_cols = sorted(c for c in df.columns if c.startswith("slip_ratio_"))
    has_slip = len(slip_cols) == 4

    results = []
    for corner in corners:
        mask = (spline >= corner.spline_start) & (spline <= corner.spline_end)
        n = int(mask.sum())
        if n < 5:
            continue

        local_spline = spline[mask]
        sp = speed_kph[mask]
        th = throttle[mask]
        br = brake[mask]

        n_zone = max(1, int(n * _ENTRY_EXIT_FRAC))
        entry_speed = float(np.nanmean(sp[:n_zone]))
        apex_speed = float(np.nanmin(sp))
        exit_speed = float(np.nanmean(sp[-n_zone:]))

        # Brake point: first sample where brake > 5%
        brake_pts = np.where(br > 0.05)[0]
        brake_point: float | None = float(local_spline[brake_pts[0]]) if len(brake_pts) else None

        # Throttle point: first sample after peak brake where throttle > 10%
        throttle_point: float | None = None
        if len(brake_pts):
            brake_peak_idx = brake_pts[np.argmax(br[brake_pts])]
            after_peak = np.where((np.arange(n) > brake_peak_idx) & (th > 0.1))[0]
            if len(after_peak):
                throttle_point = float(local_spline[after_peak[0]])

        coast_duration = int(((th < 0.06) & (br < 0.06)).sum())
        trail_brake_overlap = int(((th > 0.05) & (br > 0.05)).sum())

        max_lat_g: float | None = None
        if g_lat is not None:
            max_lat_g = float(np.nanmax(np.abs(g_lat[mask])))

        min_slip_ratio: float | None = None
        if has_slip:
            slip_stack = np.stack([df[c].values[mask] for c in slip_cols])
            min_slip_ratio = float(np.nanmin(slip_stack))

        steer_reversals = 0
        if steer is not None:
            st = steer[mask]
            if len(st) > 1:
                signs = np.sign(st)
                steer_reversals = int((np.diff(signs) != 0).sum())

        results.append({
            "corner_index": corner.index,
            "entry_speed_kph": round(entry_speed, 2),
            "apex_speed_kph": round(apex_speed, 2),
            "exit_speed_kph": round(exit_speed, 2),
            "brake_point": round(brake_point, 4) if brake_point is not None else None,
            "throttle_point": round(throttle_point, 4) if throttle_point is not None else None,
            "coast_duration": coast_duration,
            "trail_brake_overlap": trail_brake_overlap,
            "max_lat_g": round(max_lat_g, 3) if max_lat_g is not None else None,
            "min_slip_ratio": round(min_slip_ratio, 4) if min_slip_ratio is not None else None,
            "steer_reversals": steer_reversals,
        })

    return results
