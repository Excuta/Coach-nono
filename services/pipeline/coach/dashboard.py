"""
Coach Nono dashboard — Streamlit UI.

Pages
  1. Delta trace: cumulative time-delta vs PB plotted against spline position.
  2. Sector loss: ranked mini-sector table (worst first).

Run via docker-compose (dashboard service) or locally:
    streamlit run coach/dashboard.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import psycopg2
import psycopg2.extras
import streamlit as st

from coach.config import cfg

# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

@st.cache_resource
def _db_conn():
    return psycopg2.connect(cfg.database_url)


def _query(sql: str, params=()) -> list[dict]:
    c = _db_conn()
    try:
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    except Exception:
        c.rollback()
        raise


# ---------------------------------------------------------------------------
# Page layout
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Coach Nono", layout="wide")
st.title("Coach Nono")

# --- Session selector ---
sessions = _query(
    "SELECT s.id, s.car, s.track, s.session_type, s.started_at "
    "FROM sessions s ORDER BY s.started_at DESC LIMIT 50"
)

if not sessions:
    st.info("No sessions yet — drive a lap and wait for ingest + process to finish.")
    st.stop()

session_labels = {
    s["id"]: f"{s['started_at'].strftime('%Y-%m-%d %H:%M')}  {s['car']}  @  {s['track']}"
    for s in sessions
}
selected_session_id = st.selectbox(
    "Session",
    options=list(session_labels.keys()),
    format_func=lambda k: session_labels[k],
)

# --- Lap selector ---
laps = _query(
    """
    SELECT id, lap_index, lap_time, valid, status
    FROM laps WHERE session_id = %s ORDER BY lap_index
    """,
    (selected_session_id,),
)

done_laps = [l for l in laps if l["status"] == "done"]
pending_laps = [l for l in laps if l["status"] in ("pending", "processing")]

if pending_laps:
    st.caption(f"{len(pending_laps)} lap(s) still processing…")

if not done_laps:
    st.info("No processed laps in this session yet.")
    st.stop()

def _lap_label(l: dict) -> str:
    m, s = divmod(l["lap_time"], 60)
    valid = "✓" if l["valid"] else "✗"
    return f"Lap {l['lap_index']}  {int(m)}:{s:06.3f}  {valid}"

selected_lap_id = st.selectbox(
    "Lap",
    options=[l["id"] for l in done_laps],
    format_func=lambda lid: _lap_label(next(l for l in done_laps if l["id"] == lid)),
)

# --- Load delta trace ---
trace_path = cfg.findings_dir / f"{selected_lap_id}_delta.parquet"

if not trace_path.exists():
    st.info("This lap has no delta trace (it was registered as the first PB).")
    st.stop()

trace = pd.read_parquet(trace_path)

# ---------------------------------------------------------------------------
# Delta trace chart
# ---------------------------------------------------------------------------

st.subheader("Delta vs PB")

fig = go.Figure()
fig.add_trace(go.Scatter(
    x=trace["spline"],
    y=trace["delta"],
    mode="lines",
    name="Delta (s)",
    line=dict(color="#00b4d8", width=2),
))
fig.add_hline(y=0, line_dash="dash", line_color="gray", line_width=1)
fig.update_layout(
    xaxis_title="Track position (spline 0→1)",
    yaxis_title="Δ time vs PB (s)  [+ = slower]",
    height=350,
    margin=dict(l=50, r=20, t=20, b=40),
    template="plotly_dark",
)
st.plotly_chart(fig, use_container_width=True)

# Speed / inputs overlay (collapsible)
with st.expander("Inputs overlay"):
    fig2 = go.Figure()
    for col, color, name in [
        ("speed",    "#f4a261", "Speed (m/s)"),
        ("throttle", "#2a9d8f", "Throttle"),
        ("brake",    "#e63946", "Brake"),
    ]:
        if col in trace.columns:
            fig2.add_trace(go.Scatter(
                x=trace["spline"], y=trace[col],
                mode="lines", name=name,
                line=dict(color=color, width=1.5),
            ))
    fig2.update_layout(
        height=250,
        xaxis_title="Track position",
        margin=dict(l=50, r=20, t=20, b=40),
        template="plotly_dark",
    )
    st.plotly_chart(fig2, use_container_width=True)

# ---------------------------------------------------------------------------
# Sector loss table
# ---------------------------------------------------------------------------

sectors = _query(
    """
    SELECT corner, time_loss_s, severity, detail
    FROM findings WHERE lap_id = %s AND kind = 'sector_delta'
    ORDER BY time_loss_s DESC
    """,
    (selected_lap_id,),
)

st.subheader("Sector time loss")

if not sectors:
    st.caption("No sector findings for this lap.")
else:
    rows = []
    for s in sectors:
        detail = s["detail"] or {}
        rows.append({
            "Sector": s["corner"],
            "Spline": f"{detail.get('spline_start', 0):.2f}–{detail.get('spline_end', 0):.2f}",
            "Time loss (s)": f"{s['time_loss_s']:+.3f}",
            "Severity": round(s["severity"], 2),
        })
    df_sectors = pd.DataFrame(rows)

    def _color_loss(val: str) -> str:
        try:
            v = float(val)
            if v > 0.05:
                return "color: #e63946"
            if v < -0.05:
                return "color: #2a9d8f"
        except ValueError:
            pass
        return ""

    st.dataframe(
        df_sectors.style.map(_color_loss, subset=["Time loss (s)"]),
        use_container_width=True,
        hide_index=True,
    )

# ---------------------------------------------------------------------------
# Coaching notes (Tier 2 input findings)
# ---------------------------------------------------------------------------

_KIND_LABEL = {
    "trail_brake":        "Trail-brake overlap",
    "coasting":           "Coasting",
    "lockup":             "Lockup / ABS",
    "steering_reversal":  "Steering instability",
    "throttle_spike":     "Throttle spike",
    "short_shift":        "Short shift",
    "corner_overspeed":   "Corner overspeed",
}

input_findings = _query(
    """
    SELECT corner, kind, severity, detail
    FROM findings
    WHERE lap_id = %s AND kind != 'sector_delta'
    ORDER BY severity DESC, corner
    """,
    (selected_lap_id,),
)

st.subheader("Coaching notes")

if not input_findings:
    st.caption("No input coaching notes for this lap.")
else:
    rows = []
    for f in input_findings:
        detail = f.get("detail") or {}
        rows.append({
            "Sector": f["corner"],
            "Finding": _KIND_LABEL.get(f["kind"], f["kind"]),
            "Severity": round(f["severity"], 2),
            "Fix": detail.get("fix", ""),
        })
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )
