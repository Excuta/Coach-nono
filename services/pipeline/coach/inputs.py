"""
Tier 2 input-coaching detectors.

Each detector takes an aligned DataFrame (GRID_N rows, uniform spline grid from
align.py) and returns a list of finding dicts ready for the findings DB table:
    {kind, corner, severity, time_loss_s, detail}

Thresholds are car/track-configurable via data/config/thresholds.json.
Defaults are tuned for GT3 cars; adjust per combo as you accumulate real laps.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

N_SECTORS = 20  # must match delta.py


# ---------------------------------------------------------------------------
# Threshold config
# ---------------------------------------------------------------------------

@dataclass
class Thresholds:
    # trail-brake overlap: throttle on while braking simultaneously
    trail_throttle_min: float = 0.061  # throttle fraction considered "on"
    trail_brake_min: float = 0.182     # brake fraction considered "on"
    trail_min_duration: int = 12       # min contiguous grid points to flag (~1.2% of lap)

    # coasting: neither throttle nor brake applied (sustained)
    coast_throttle_max: float = 0.061
    coast_brake_max: float = 0.061
    coast_min_duration: int = 36       # min grid points (~3.6% of lap)

    # lockup / ABS engagement
    lockup_brake_min: float = 0.97     # brake fraction for heavy braking zone
    lockup_decel_threshold: float = 0.0048  # speed drop per grid point (m/s) to flag lockup

    # steering reversals (wheel sawing / instability)
    reversal_window: int = 61          # half-window size in grid points
    reversal_max_count: int = 7        # sign-change count within window to flag

    # throttle spike / roughness
    throttle_spike_delta: float = 0.24  # throttle change per grid step to flag
    throttle_spike_min: int = 3         # min consecutive spiky points

    # short shift (upshift too early)
    short_shift_rpm_frac: float = 0.908  # flag upshift below this fraction of session max RPM

    # corner overspeed: braking while mid-corner (steer + brake simultaneously)
    overspeed_brake_min: float = 0.36
    overspeed_steer_min: float = 0.24
    overspeed_min_duration: int = 19   # grid points


def load_thresholds(car: str, track: str, config_path: Path | None = None) -> Thresholds:
    """
    Return Thresholds, applying car/track overrides from config_path if present.

    config_path must be a JSON file structured as:
        {
            "default": { "coast_min_duration": 25 },
            "lamborghini_huracan_gt3_evo2_monza": { "short_shift_rpm_frac": 0.80 }
        }
    Car/track key = f"{car}_{track}".lower().replace(" ", "_").
    """
    t = Thresholds()
    if config_path is None or not config_path.exists():
        return t

    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Could not load thresholds config %s: %s", config_path, exc)
        return t

    def _apply(overrides: dict) -> None:
        for k, v in overrides.items():
            if hasattr(t, k):
                setattr(t, k, type(getattr(t, k))(v))
            else:
                log.warning("Unknown threshold key in config: %s", k)

    if "default" in data:
        _apply(data["default"])

    combo_key = f"{car}_{track}".lower().replace(" ", "_")
    if combo_key in data:
        _apply(data[combo_key])

    return t


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sector(spline: float) -> int:
    return min(int(spline * N_SECTORS), N_SECTORS - 1)


def _segments(mask: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    """Return (start, end) index pairs for contiguous True runs >= min_len."""
    segs: list[tuple[int, int]] = []
    in_seg = False
    start = 0
    for i, v in enumerate(mask):
        if v and not in_seg:
            in_seg, start = True, i
        elif not v and in_seg:
            in_seg = False
            if i - start >= min_len:
                segs.append((start, i))
    if in_seg and len(mask) - start >= min_len:
        segs.append((start, len(mask)))
    return segs


def _dedup_by_sector(findings: list[dict]) -> list[dict]:
    """Keep only the highest-severity finding per sector."""
    best: dict[int, dict] = {}
    for f in findings:
        sec = f["corner"]
        if sec not in best or f["severity"] > best[sec]["severity"]:
            best[sec] = f
    return list(best.values())


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def _detect_trail_brake(df: pd.DataFrame, t: Thresholds, spline: np.ndarray) -> list[dict]:
    if "throttle" not in df.columns or "brake" not in df.columns:
        return []
    throttle = df["throttle"].to_numpy()
    brake = df["brake"].to_numpy()
    mask = (throttle > t.trail_throttle_min) & (brake > t.trail_brake_min)
    findings = []
    for s, e in _segments(mask, t.trail_min_duration):
        peak_i = s + int(np.argmax(throttle[s:e] * brake[s:e]))
        severity = float(min(1.0, throttle[peak_i] * brake[peak_i] / 0.3))
        findings.append({
            "kind": "trail_brake",
            "corner": _sector(float(spline[peak_i])),
            "severity": round(severity, 3),
            "time_loss_s": 0.0,
            "detail": {
                "spline": round(float(spline[peak_i]), 4),
                "duration_pts": e - s,
                "fix": "Ease off throttle before braking — simultaneous inputs unsettle the car on entry.",
            },
        })
    return findings


def _detect_coasting(df: pd.DataFrame, t: Thresholds, spline: np.ndarray) -> list[dict]:
    if "throttle" not in df.columns or "brake" not in df.columns:
        return []
    throttle = df["throttle"].to_numpy()
    brake = df["brake"].to_numpy()
    mask = (throttle < t.coast_throttle_max) & (brake < t.coast_brake_max)
    mask[:5] = False  # ignore the very start of the lap
    findings = []
    for s, e in _segments(mask, t.coast_min_duration):
        duration_pts = e - s
        severity = float(min(1.0, duration_pts / 80))
        peak_i = s + (e - s) // 2
        findings.append({
            "kind": "coasting",
            "corner": _sector(float(spline[peak_i])),
            "severity": round(severity, 3),
            "time_loss_s": 0.0,
            "detail": {
                "spline_start": round(float(spline[s]), 4),
                "spline_end": round(float(spline[e - 1]), 4),
                "duration_pts": duration_pts,
                "fix": "Commit to throttle or brake — unnecessary coasting bleeds momentum.",
            },
        })
    return findings


def _detect_lockup(df: pd.DataFrame, t: Thresholds, spline: np.ndarray) -> list[dict]:
    findings = []

    # Prefer abs_active flag if the data has it
    if "abs_active" in df.columns and df["abs_active"].notna().any():
        abs_arr = df["abs_active"].fillna(False).to_numpy().astype(bool)
        for s, e in _segments(abs_arr, 3):
            peak_i = s + (e - s) // 2
            brake_val = float(df["brake"].iloc[peak_i]) if "brake" in df.columns else 1.0
            findings.append({
                "kind": "lockup",
                "corner": _sector(float(spline[peak_i])),
                "severity": round(min(1.0, brake_val), 3),
                "time_loss_s": 0.0,
                "detail": {
                    "spline": round(float(spline[peak_i]), 4),
                    "fix": "Ease brake pressure — ABS firing means you are over the tyre limit.",
                },
            })
        return findings

    # Fallback: heavy brake + sharp speed drop
    if "brake" not in df.columns or "speed" not in df.columns:
        return []
    brake = df["brake"].to_numpy()
    speed = df["speed"].to_numpy()
    decel = np.diff(speed, prepend=speed[0])
    mask = (brake > t.lockup_brake_min) & (decel < -t.lockup_decel_threshold)
    for s, e in _segments(mask, 3):
        peak_i = s + int(np.argmax(brake[s:e]))
        findings.append({
            "kind": "lockup",
            "corner": _sector(float(spline[peak_i])),
            "severity": round(min(1.0, float(brake[peak_i])), 3),
            "time_loss_s": 0.0,
            "detail": {
                "spline": round(float(spline[peak_i]), 4),
                "fix": "Ease brake pressure — you are locking up and losing stopping efficiency.",
            },
        })
    return findings


def _detect_steering_reversal(df: pd.DataFrame, t: Thresholds, spline: np.ndarray) -> list[dict]:
    if "steer" not in df.columns:
        return []
    steer = df["steer"].to_numpy()
    sign = np.sign(steer)
    reversals = np.abs(np.diff(sign, prepend=sign[0])) > 1

    findings = []
    w = t.reversal_window
    step = max(1, w // 2)
    for i in range(w, len(reversals) - w, step):
        count = int(reversals[i - w: i + w].sum())
        if count >= t.reversal_max_count:
            severity = float(min(1.0, count / (t.reversal_max_count * 2)))
            findings.append({
                "kind": "steering_reversal",
                "corner": _sector(float(spline[i])),
                "severity": round(severity, 3),
                "time_loss_s": 0.0,
                "detail": {
                    "spline": round(float(spline[i]), 4),
                    "reversal_count": count,
                    "fix": "Stabilise the wheel — excess corrections waste grip and upset the chassis.",
                },
            })
    return _dedup_by_sector(findings)


def _detect_throttle_spike(df: pd.DataFrame, t: Thresholds, spline: np.ndarray) -> list[dict]:
    if "throttle" not in df.columns:
        return []
    throttle = df["throttle"].to_numpy()
    delta = np.abs(np.diff(throttle, prepend=throttle[0]))
    mask = delta > t.throttle_spike_delta
    findings = []
    for s, e in _segments(mask, t.throttle_spike_min):
        peak_i = s + int(np.argmax(delta[s:e]))
        severity = float(min(1.0, delta[peak_i] / (t.throttle_spike_delta * 2)))
        findings.append({
            "kind": "throttle_spike",
            "corner": _sector(float(spline[peak_i])),
            "severity": round(severity, 3),
            "time_loss_s": 0.0,
            "detail": {
                "spline": round(float(spline[peak_i]), 4),
                "fix": "Smoother throttle application — sudden inputs unsettle the rear on exit.",
            },
        })
    return _dedup_by_sector(findings)


def _detect_short_shift(df: pd.DataFrame, t: Thresholds, spline: np.ndarray) -> list[dict]:
    if "gear" not in df.columns or "rpm" not in df.columns:
        return []
    gear = df["gear"].to_numpy()
    rpm = df["rpm"].to_numpy()
    upshifts = np.where(np.diff(gear) == 1)[0]
    if len(upshifts) == 0:
        return []

    max_rpm = float(np.nanpercentile(rpm, 99))
    if max_rpm < 1000:  # unreliable data
        return []

    threshold_rpm = t.short_shift_rpm_frac * max_rpm
    findings = []
    for i in upshifts:
        shift_rpm = float(rpm[i])
        if shift_rpm < threshold_rpm:
            deficit_frac = (threshold_rpm - shift_rpm) / max_rpm
            severity = float(min(1.0, deficit_frac * 4))
            findings.append({
                "kind": "short_shift",
                "corner": _sector(float(spline[i])),
                "severity": round(severity, 3),
                "time_loss_s": 0.0,
                "detail": {
                    "spline": round(float(spline[i]), 4),
                    "rpm_at_shift": int(shift_rpm),
                    "threshold_rpm": int(threshold_rpm),
                    "fix": (
                        f"Upshift too early — {int(shift_rpm)} rpm vs "
                        f"{int(threshold_rpm)} rpm threshold. Hold the gear longer for more exit drive."
                    ),
                },
            })
    return findings


def _detect_corner_overspeed(df: pd.DataFrame, t: Thresholds, spline: np.ndarray) -> list[dict]:
    """Detect braking while mid-corner (high steer angle + significant brake)."""
    if "brake" not in df.columns or "steer" not in df.columns:
        return []
    brake = df["brake"].to_numpy()
    steer = np.abs(df["steer"].to_numpy())
    mask = (brake > t.overspeed_brake_min) & (steer > t.overspeed_steer_min)
    findings = []
    for s, e in _segments(mask, t.overspeed_min_duration):
        peak_i = s + int(np.argmax(brake[s:e] * steer[s:e]))
        severity = float(min(1.0, (brake[peak_i] * steer[peak_i]) / 0.25))
        findings.append({
            "kind": "corner_overspeed",
            "corner": _sector(float(spline[peak_i])),
            "severity": round(severity, 3),
            "time_loss_s": 0.0,
            "detail": {
                "spline": round(float(spline[peak_i]), 4),
                "fix": "Braking mid-corner — enter slower or brake earlier to carry more speed through.",
            },
        })
    return _dedup_by_sector(findings)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

_DETECTORS = [
    _detect_trail_brake,
    _detect_coasting,
    _detect_lockup,
    _detect_steering_reversal,
    _detect_throttle_spike,
    _detect_short_shift,
    _detect_corner_overspeed,
]


def detect(aligned: pd.DataFrame, thresholds: Thresholds | None = None) -> list[dict]:
    """
    Run all input detectors on an aligned lap DataFrame.
    Returns a list of finding dicts (kind, corner, severity, time_loss_s, detail).
    """
    if thresholds is None:
        thresholds = Thresholds()

    spline = aligned["spline"].to_numpy()
    findings: list[dict] = []
    for detector in _DETECTORS:
        try:
            findings.extend(detector(aligned, thresholds, spline))
        except Exception:
            log.exception("Detector %s failed", detector.__name__)
    return findings
