"""Template-based coaching card generator.

Takes the lap's findings (already computed by the analysis pipeline) and
produces a human-readable markdown coaching card. No LLM, no GPU.
"""
from __future__ import annotations


def _fmt_time(seconds: float) -> str:
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:06.3f}"


# Per-kind sentence templates.
# Available substitutions: val, p50, corner (name string)
_TEMPLATES: dict[str, str] = {
    "slow_apex": (
        "Apex {val:.0f} km/h vs your usual {p50:.0f} km/h — "
        "try a later turn-in point or trust the car more on entry"
    ),
    "late_brake": (
        "Brake point {val:.3f} vs usual {p50:.3f} — "
        "move the brake point earlier by a few car lengths"
    ),
    "delayed_throttle": (
        "Throttle at {val:.3f} vs usual {p50:.3f} — "
        "open the throttle sooner on corner exit"
    ),
    "coasting": (
        "Coasting longer than usual ({val:.0f} samples) — "
        "avoid no-man's land: commit to throttle or brake earlier"
    ),
    "trail_brake": (
        "High throttle/brake overlap — "
        "finish braking fully before feeding in the throttle"
    ),
    "steering_instability": (
        "More steering corrections than usual ({val:.0f}) — "
        "commit to one clean steering input on turn-in"
    ),
}

_KIND_LABEL: dict[str, str] = {
    "slow_apex":            "Slow apex",
    "late_brake":           "Late brake",
    "delayed_throttle":     "Late throttle",
    "coasting":             "Coasting",
    "trail_brake":          "Trail brake",
    "steering_instability": "Steer corrections",
}

_FOCUS_LABEL: dict[str, str] = {
    "slow_apex":            "apex speed",
    "late_brake":           "brake point",
    "delayed_throttle":     "throttle application",
    "coasting":             "coast reduction",
    "trail_brake":          "brake/throttle overlap",
    "steering_instability": "steering stability",
}


def _render_finding(f: dict, corner_name: str) -> str | None:
    kind = f.get("kind", "")
    d    = f.get("detail") or {}
    val  = d.get("value")
    p50  = d.get("p50")
    tmpl = _TEMPLATES.get(kind)
    if tmpl is None:
        return None
    try:
        text = tmpl.format(val=val if val is not None else 0,
                           p50=p50 if p50 is not None else 0)
    except (KeyError, TypeError, ValueError):
        text = d.get("fix", kind)
    label = _KIND_LABEL.get(kind, kind)
    return f"- **{corner_name}** · {label}: {text}"


def build_coaching_card(
    findings: list[dict],
    lap_time: float,
    total_delta: float | None,
    corner_names: dict[int, str],
    setup_advice: list[dict] | None = None,
) -> str:
    """Return a markdown coaching card for one lap.

    findings:      rows from the findings table (all kinds)
    lap_time:      lap time in seconds
    total_delta:   cumulative Δ vs PB at lap end (positive = slower), or None
    corner_names:  {corner_index: name}  e.g. {1: "T1", 3: "T3"}
    """

    sector_losses = sorted(
        [f for f in findings
         if f["kind"] == "sector_delta" and (f.get("time_loss_s") or 0) > 0.05],
        key=lambda f: -(f.get("time_loss_s") or 0),
    )

    technique = sorted(
        [f for f in findings
         if (f.get("detail") or {}).get("source") == "corner_baseline"],
        key=lambda f: -f["severity"],
    )

    # ── Header ────────────────────────────────────────────────────────────────
    header = f"### Lap {_fmt_time(lap_time)}"
    if total_delta is not None:
        sign = "+" if total_delta >= 0 else ""
        header += f"  ·  Δ {sign}{total_delta:.3f}s vs PB"

    lines = [header, ""]

    # ── Time losses ───────────────────────────────────────────────────────────
    if sector_losses:
        lines.append("**⏱ Time losses vs PB**")
        for f in sector_losses[:5]:
            d    = f.get("detail") or {}
            sec  = f.get("corner", "?")
            loss = f.get("time_loss_s") or 0.0
            s_range = ""
            if "spline_start" in d and "spline_end" in d:
                s_range = f" (spline {d['spline_start']:.2f}–{d['spline_end']:.2f})"
            lines.append(f"- Sector {sec}{s_range}: **−{loss:.3f}s**")
        lines.append("")

    # ── Technique findings ────────────────────────────────────────────────────
    if technique:
        lines.append("**🔧 Technique**")
        seen: set[tuple] = set()
        rendered = 0
        for f in technique:
            cidx  = f.get("corner")
            cname = corner_names.get(cidx, f"T{cidx}") if cidx is not None else "—"
            key   = (cidx, f["kind"])
            if key in seen:
                continue
            seen.add(key)
            line = _render_finding(f, cname)
            if line:
                lines.append(line)
                rendered += 1
            if rendered >= 8:
                break
        lines.append("")

    # ── Focus for next session ────────────────────────────────────────────────
    focus: list[str] = []
    for f in technique[:3]:
        cidx  = f.get("corner")
        cname = corner_names.get(cidx, f"T{cidx}") if cidx is not None else "—"
        label = _FOCUS_LABEL.get(f["kind"], f["kind"])
        focus.append(f"{cname} {label}")

    if sector_losses and len(focus) < 3:
        sf = sector_losses[0]
        focus.append(f"sector {sf.get('corner','?')} entry ({sf.get('time_loss_s',0):.3f}s)")

    if focus:
        lines.append("**🎯 Focus next session**")
        for i, item in enumerate(focus[:3], 1):
            lines.append(f"{i}. {item}")
        lines.append("")

    # ── Setup hints ───────────────────────────────────────────────────────────
    if setup_advice:
        lines.append("**⚙️ Setup**")
        for adv in setup_advice[:2]:
            lines.append(f"- *{adv['symptom']}*")
            for hint in adv["hints"]:
                lines.append(f"  - {hint}")
            if adv.get("note"):
                lines.append(f"  _{adv['note']}_")
        lines.append("")

    # ── Empty state ───────────────────────────────────────────────────────────
    if not sector_losses and not technique:
        lines.append("_No significant findings for this lap._")
        lines.append("")
        lines.append("_Baselines need ≥5 valid laps per corner to activate — "
                     "keep driving to build them up._")

    return "\n".join(lines)
