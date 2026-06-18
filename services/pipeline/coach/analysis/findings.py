"""Statistical finding generation from corner baselines.

Flags a corner metric when its z-score (relative to the driver's own baseline)
exceeds a per-metric threshold. No user-facing thresholds — sensitivity is derived
from the driver's own lap distribution.
"""
from __future__ import annotations

from .db_ops import MIN_BASELINE_SAMPLES

# (metric, direction, z_threshold, kind, fix)
# direction "low"  → flag when value is below baseline (z < -thresh)
# direction "high" → flag when value is above baseline (z > thresh)
_CONFIGS: list[tuple[str, str, float, str, str]] = [
    (
        "apex_speed_kph", "low", 1.5,
        "slow_apex",
        "Entry or mid-corner speed too low — trust the car more on entry",
    ),
    (
        "brake_point", "low", 1.5,
        "late_brake",
        "Braking later than your baseline — move the brake point earlier",
    ),
    (
        "throttle_point", "high", 1.5,
        "delayed_throttle",
        "Throttle applied later than usual — commit sooner on corner exit",
    ),
    (
        "coast_duration", "high", 1.5,
        "coasting",
        "More coasting than usual — commit to throttle or brake, avoid the gap",
    ),
    (
        "trail_brake_overlap", "high", 2.0,
        "trail_brake",
        "Unusually high throttle/brake overlap — release brake before opening throttle",
    ),
    (
        "steer_reversals", "high", 2.0,
        "steering_instability",
        "More steering corrections than usual — settle hands earlier on entry",
    ),
]


def score_corner(corner_index: int, stats: dict, baselines: dict) -> list[dict]:
    """Return statistical findings for one lap's corner metrics vs driver baselines.

    Returns a list of finding dicts compatible with the existing findings table schema.
    Empty list when baselines are in "learning" phase (< MIN_BASELINE_SAMPLES).
    """
    findings = []
    for metric, direction, z_thresh, kind, fix in _CONFIGS:
        b = baselines.get(metric)
        val = stats.get(metric)
        if b is None or val is None:
            continue
        if (b.get("sample_count") or 0) < MIN_BASELINE_SAMPLES:
            continue
        stddev = b.get("stddev") or 0.0
        if stddev < 1e-6:
            continue

        z = (val - b["p50"]) / stddev
        triggered = (direction == "low" and z < -z_thresh) or \
                    (direction == "high" and z > z_thresh)

        if not triggered:
            continue

        severity = min(1.0, abs(z) / (z_thresh * 2.0))
        findings.append({
            "corner": corner_index,
            "kind": kind,
            "severity": round(severity, 3),
            "time_loss_s": None,
            "detail": {
                "metric": metric,
                "value": round(val, 3) if isinstance(val, float) else val,
                "p50": round(b["p50"], 3) if b["p50"] is not None else None,
                "z_score": round(z, 2),
                "fix": fix,
                "sample_count": b["sample_count"],
                "source": "corner_baseline",
            },
        })

    return findings
