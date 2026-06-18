"""Rule-based setup direction advisor.

Identifies setup-level patterns from corner findings (multiple-corner trends
that suggest a handling imbalance rather than a one-off technique error).
Returns directional hints — the driver decides what to actually change.
"""
from __future__ import annotations

from collections import Counter

# Minimum corners showing the same symptom before it's called a setup issue
_MIN_CORNERS = 2

_HINTS: dict[str, list[str]] = {
    "understeer": [
        "Soften front ARB (anti-roll bar)",
        "Reduce front negative camber by 0.1–0.2°",
        "Move brake bias rearward 1–2 steps",
        "Lower front tyre pressure 0.1–0.2 bar",
    ],
    "oversteer": [
        "Soften rear ARB",
        "Reduce rear negative camber by 0.1°",
        "Move brake bias forward 1 step",
        "Lower rear tyre pressure 0.1 bar",
    ],
    "instability": [
        "Increase rear bump damping",
        "Add rear toe-in (reduce toe-out)",
        "Raise rear ride height slightly",
    ],
    "traction": [
        "Soften rear spring or rear ARB",
        "Lower TC level by 1–2 steps",
        "Increase rear tyre pressure 0.1 bar",
    ],
    "brake_balance": [
        "Move brake bias rearward 1 step",
        "Check brake duct sizing vs current temps",
    ],
}


def advise_setup(findings: list[dict]) -> list[dict]:
    """Return setup advice dicts: {symptom, hints, note?, priority}.

    Only fires when ≥MIN_CORNERS corners show the same pattern — single-corner
    issues are flagged as technique, not setup.
    """
    kind_count: Counter = Counter()
    for f in findings:
        if (f.get("detail") or {}).get("source") == "corner_baseline":
            kind_count[f["kind"]] += 1

    n_slow    = kind_count["slow_apex"]
    n_thr     = kind_count["delayed_throttle"]
    n_steer   = kind_count["steering_instability"]
    n_brake   = kind_count["late_brake"]
    n_trail   = kind_count["trail_brake"]

    advice: list[dict] = []

    # Understeer: slow apexes without steering instability (car pushes, not snaps)
    if n_slow >= _MIN_CORNERS and n_steer < _MIN_CORNERS:
        advice.append({
            "symptom": f"Slow apex at {n_slow} corner(s) — likely understeer on entry",
            "hints": _HINTS["understeer"][:2],
            "priority": 1,
        })

    # Oversteer: slow apexes + high steering corrections (car snapping on turn-in)
    if n_slow >= _MIN_CORNERS and n_steer >= _MIN_CORNERS:
        advice.append({
            "symptom": (
                f"Slow apex + steering corrections at {n_steer} corner(s)"
                " — car unsettled on entry"
            ),
            "hints": _HINTS["oversteer"][:2],
            "priority": 1,
        })

    # Instability without slow apex (mid-corner or exit snap)
    elif n_steer >= _MIN_CORNERS and n_slow < _MIN_CORNERS:
        advice.append({
            "symptom": f"Steering corrections at {n_steer} corner(s) — nervous mid-corner",
            "hints": _HINTS["instability"][:2],
            "priority": 2,
        })

    # Traction: delayed throttle at multiple corners
    if n_thr >= _MIN_CORNERS:
        advice.append({
            "symptom": f"Late throttle at {n_thr} corner(s) — traction or exit oversteer",
            "hints": _HINTS["traction"][:2],
            "note": "Rule out exit technique first — try one change at a time",
            "priority": 2,
        })

    # Brake: lots of late-brake + trail overlap suggests brake imbalance
    if n_brake >= _MIN_CORNERS and n_trail >= _MIN_CORNERS:
        advice.append({
            "symptom": f"Late brake + trail overlap at {n_brake} corner(s) — brake balance",
            "hints": _HINTS["brake_balance"],
            "priority": 3,
        })

    return sorted(advice, key=lambda a: a["priority"])
