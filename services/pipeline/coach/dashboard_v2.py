"""Coach Nono v2 dashboard — full telemetry, all channels."""
from __future__ import annotations

from collections import defaultdict

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import psycopg2
import psycopg2.extras
import streamlit as st
from plotly.subplots import make_subplots
from scipy.interpolate import interp1d

from coach.align import GRID_N, _extract_flying_lap
from coach.config import cfg

# ---------------------------------------------------------------------------
# Color constants
# ---------------------------------------------------------------------------

C = dict(
    throttle  = "#00C853", brake     = "#FF1744",
    speed     = "#ECEFF1", delta_pos = "#FF1744", delta_neg = "#00C853",
    g_lat     = "#FF9800", g_lon     = "#00BCD4", g_vert    = "#9C27B0",
    FL        = "#2196F3", FR        = "#4CAF50", RL        = "#FF9800", RR = "#F44336",
    steer_l   = "#448AFF", steer_r   = "#FF6D00",
    rpm       = "#FFD600", gear      = "#B0BEC5", fuel      = "#80CBC4",
    yaw       = "#E040FB", grid_line = "#37474F",
    bg        = "#0E1117", panel     = "#11151C",
    clutch    = "#78909C", water_t   = "#4CAF50", exhaust   = "#FF6D00",
    boost     = "#00BCD4", kerb_vib  = "#FF8F00", slip_vib  = "#E91E63",
)
WHEEL_LABELS  = ["fl", "fr", "rl", "rr"]
WHEEL_DISPLAY = ["FL", "FR", "RL", "RR"]
WHEEL_COLORS  = [C["FL"], C["FR"], C["RL"], C["RR"]]

_TUPLE4_COLS = [
    "tyre_temp", "tyre_press",
    "brake_temp", "brake_pressure", "pad_life", "disc_life",
    "wheel_slip", "slip_ratio", "slip_angle", "wheel_angular_s",
    "suspension_travel", "suspension_damage",
]
_CAR_DMG_LABELS = ["fl", "fr", "rl", "rr", "center"]

# ---------------------------------------------------------------------------
# Enum maps
# ---------------------------------------------------------------------------

FLAG_LABEL  = {0: "None", 1: "🟡 Yellow", 2: "🔴 Red", 4: "🏳 White", 8: "🏁 Chequered"}
GRIP_LABEL  = {0: "Optimum", 1: "Green", 2: "Warning", 3: "Low"}
GRIP_COLOR  = {0: "#00C853", 1: "#8BC34A", 2: "#FFB300", 3: "#FF1744"}
RAIN_LABEL  = {0: "Dry ☀", 1: "Drizzle 🌦", 2: "Light rain 🌧", 3: "Heavy rain ⛈"}
KIND_LABEL  = {
    "trail_brake":       "Trail-brake",     "coasting":        "Coasting",
    "lockup":            "Lockup/ABS",      "steering_reversal":"Steer instability",
    "throttle_spike":    "Throttle spike",  "short_shift":     "Short shift",
    "corner_overspeed":  "Corner overspeed",
}
KIND_COLOR  = {
    "trail_brake": "#f4a261", "coasting": "#a8dadc", "lockup": "#e63946",
    "steering_reversal": "#8338ec", "throttle_spike": "#ffb703",
    "short_shift": "#06d6a0", "corner_overspeed": "#ef233c",
}

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
# align_extended: raw parquet → uniform 1000-pt grid with ALL channels
# ---------------------------------------------------------------------------

def _safe_idx(x, i: int) -> float:
    try:
        if x is None:
            return np.nan
        return float(x[i]) if len(x) > i else np.nan
    except Exception:
        return np.nan


def _expand_tuples(seg: pd.DataFrame) -> pd.DataFrame:
    seg = seg.copy()
    for col in _TUPLE4_COLS:
        if col not in seg.columns or not seg[col].notna().any():
            continue
        for i, lbl in enumerate(WHEEL_LABELS):
            seg[f"{col}_{lbl}"] = seg[col].apply(_safe_idx, i=i)
        seg.drop(columns=[col], inplace=True)
    for col, labels in [("car_damage", _CAR_DMG_LABELS), ("suspension_damage", WHEEL_LABELS)]:
        if col not in seg.columns or not seg[col].notna().any():
            continue
        for i, lbl in enumerate(labels):
            seg[f"{col}_{lbl}"] = seg[col].apply(_safe_idx, i=i)
        seg.drop(columns=[col], inplace=True)
    return seg


_BOOL_COLS = {
    "abs_active", "tc_active", "is_in_pit", "is_in_pit_lane",
    "autoshifter_on", "pit_limiter_on", "is_ai_controlled",
    "global_yellow_s1", "global_yellow_s2", "global_yellow_s3",
}
_STEP_COLS = {
    "gear", "flag", "track_grip_status", "current_sector_index",
    "tc_level", "tc_cut_level", "abs_level", "engine_map",
    "position", "rain_10min", "rain_30min",
    "front_brake_compound", "rear_brake_compound",
}


@st.cache_data(show_spinner="Resampling telemetry…")
def align_extended(lap_path: str) -> pd.DataFrame | None:
    full = cfg.data_dir / lap_path
    if not full.exists():
        return None
    try:
        raw = pd.read_parquet(full)
        seg = _extract_flying_lap(raw).copy()
        seg["lap_elapsed"] = (seg["t"] - seg["t"].min()).clip(lower=0.0)
        seg = _expand_tuples(seg)
        seg = seg.sort_values("spline").drop_duplicates(subset="spline", keep="first")
        if len(seg) < 10:
            return None
        grid = np.linspace(0.0, 1.0, GRID_N)
        sp = seg["spline"].to_numpy()
        out: dict[str, np.ndarray] = {"spline": grid}
        for col in seg.columns:
            if col == "spline":
                continue
            series = seg[col]
            if series.isna().all():
                continue
            try:
                v = series.to_numpy(dtype=float, na_value=np.nan)
            except (ValueError, TypeError):
                continue
            mask = ~np.isnan(v)
            if mask.sum() < 2:
                continue
            kind = "previous" if col in _STEP_COLS else "linear"
            f = interp1d(sp[mask], v[mask], kind=kind, bounds_error=False,
                         fill_value=(float(v[mask][0]), float(v[mask][-1])))
            res = f(grid)
            if col in _BOOL_COLS:
                res = np.round(res).clip(0, 1)
            out[col] = res
        return pd.DataFrame(out)
    except Exception as e:
        st.warning(f"Telemetry resample error: {e}")
        return None

# ---------------------------------------------------------------------------
# Cached loaders
# ---------------------------------------------------------------------------

@st.cache_data
def load_delta(lap_id: str) -> pd.DataFrame | None:
    p = cfg.findings_dir / f"{lap_id}_delta.parquet"
    return pd.read_parquet(p) if p.exists() else None


@st.cache_data
def load_extras(lap_id: str) -> dict:
    rows = _query("SELECT * FROM extras WHERE lap_id = %s", (lap_id,))
    return rows[0] if rows else {}


@st.cache_data
def load_findings(lap_id: str) -> list[dict]:
    return _query(
        "SELECT corner, kind, severity, time_loss_s, detail "
        "FROM findings WHERE lap_id = %s",
        (lap_id,),
    )


@st.cache_data
def load_coords(lap_id: str) -> pd.DataFrame | None:
    rows = _query("SELECT coords_path FROM coordinates WHERE lap_id = %s", (lap_id,))
    if not rows:
        return None
    p = cfg.data_dir / rows[0]["coords_path"]
    return pd.read_parquet(p) if p.exists() else None

# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------

def _has(df: pd.DataFrame | None, col: str) -> bool:
    return df is not None and col in df.columns and df[col].notna().any()


def _sector_lines(fig: go.Figure, n: int = 20, rows: list[int] | None = None):
    for i in range(1, n):
        kw: dict = dict(x=i / n, line_color=C["grid_line"], line_width=0.5, line_dash="dot")
        if rows:
            for r in rows:
                fig.add_vline(**kw, row=r, col=1)  # type: ignore[arg-type]
        else:
            fig.add_vline(**kw)


def _base_layout(fig: go.Figure, height: int, margin_l: int = 70) -> go.Figure:
    fig.update_layout(
        template="plotly_dark", paper_bgcolor=C["bg"], plot_bgcolor=C["panel"],
        height=height, margin=dict(l=margin_l, r=20, t=20, b=30),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0, font_size=11),
    )
    return fig

# ---------------------------------------------------------------------------
# Sidebar + shared selectors (run on every render)
# ---------------------------------------------------------------------------

st.set_page_config(page_title="Coach Nono v2", layout="wide", page_icon="🏎")
st.markdown("<style>[data-testid='stSidebar']{background:#0a0d12}</style>",
            unsafe_allow_html=True)

with st.sidebar:
    st.title("🏎 Coach Nono v2")
    page = st.radio("View", ["① Lap Telemetry", "② Track Map", "③ Session & Health"],
                    label_visibility="collapsed")
    st.divider()

    sessions = _query(
        "SELECT s.id, s.car, s.track, s.session_type, s.started_at,"
        " s.statics->>'player_name'           AS player_name,"
        " s.statics->>'max_rpm'               AS max_rpm,"
        " s.statics->>'max_fuel'              AS max_fuel,"
        " s.statics->>'sector_count'          AS sector_count,"
        " s.statics->>'is_online'             AS is_online,"
        " s.statics->>'aid_fuel_rate'         AS aid_fuel_rate,"
        " s.statics->>'aid_tyre_rate'         AS aid_tyre_rate,"
        " s.statics->>'aid_mechanical_damage' AS aid_mechanical_damage,"
        " s.statics->>'aid_stability'         AS aid_stability,"
        " s.statics->>'aid_auto_clutch'       AS aid_auto_clutch,"
        " s.statics->>'penalty_enabled'       AS penalty_enabled"
        " FROM sessions s ORDER BY s.started_at DESC LIMIT 50"
    )

    if not sessions:
        st.info("No sessions yet — drive a lap and wait for processing.")
        st.stop()

    sess_lbl = {
        s["id"]: f"{s['started_at'].strftime('%Y-%m-%d %H:%M')}  {s['car']}  @  {s['track']}"
        for s in sessions
    }
    sel_sess_id = st.selectbox("Session", list(sess_lbl), format_func=sess_lbl.__getitem__)
    sel_sess = next(s for s in sessions if s["id"] == sel_sess_id)

    all_laps = _query(
        "SELECT id, lap_index, lap_time, valid, status, lap_path "
        "FROM laps WHERE session_id = %s ORDER BY lap_index",
        (sel_sess_id,),
    )
    done_laps = [l for l in all_laps if l["status"] == "done"]
    pending   = [l for l in all_laps if l["status"] in ("pending", "processing")]
    if pending:
        st.caption(f"{len(pending)} lap(s) still processing…")
    if not done_laps:
        st.info("No processed laps yet.")
        st.stop()

    def _lap_lbl(l: dict) -> str:
        m, s = divmod(l["lap_time"], 60)
        return f"Lap {l['lap_index']}  {int(m)}:{s:06.3f}  {'✓' if l['valid'] else '✗'}"

    sel_lap_id = st.selectbox("Lap", [l["id"] for l in done_laps],
                               format_func=lambda lid: _lap_lbl(
                                   next(l for l in done_laps if l["id"] == lid)))
    sel_lap = next(l for l in done_laps if l["id"] == sel_lap_id)

    compare_opts = {"PB": "PB"} | {l["id"]: _lap_lbl(l) for l in done_laps if l["id"] != sel_lap_id}
    sel_compare  = st.selectbox("Compare vs", list(compare_opts), format_func=compare_opts.__getitem__)

    st.divider()
    player  = sel_sess.get("player_name") or "—"
    is_onl  = sel_sess.get("is_online") in (True, "True", "true", "1")
    st.caption(f"👤 {player}  ·  {sel_sess['session_type']}  ·  {'Online' if is_onl else 'Offline'}")

# ---------------------------------------------------------------------------
# Load data for selected lap
# ---------------------------------------------------------------------------

gx        = align_extended(sel_lap["lap_path"])
delta_df  = load_delta(sel_lap_id)
extras    = load_extras(sel_lap_id)
findings  = load_findings(sel_lap_id)
coords_df = load_coords(sel_lap_id)

try:    max_rpm  = int(float(sel_sess.get("max_rpm") or 0))
except: max_rpm  = 0
try:    max_fuel = float(sel_sess.get("max_fuel") or 0)
except: max_fuel = 0.0

# PB / compare ghost
if sel_compare == "PB":
    pb_df = _query("SELECT l.id FROM pbs p JOIN laps l ON l.id=p.lap_id "
                   "WHERE p.game=%s AND p.car=%s AND p.track=%s LIMIT 1",
                   (sel_sess.get("game", "acc"), sel_sess["car"], sel_sess["track"]))
    ghost_lap_path = pb_df[0]["id"] if pb_df else None
    ghost_gx = align_extended(
        next((l["lap_path"] for l in done_laps if l["id"] == ghost_lap_path), "")
    ) if ghost_lap_path else None
else:
    ghost_lap = next((l for l in done_laps if l["id"] == sel_compare), None)
    ghost_gx  = align_extended(ghost_lap["lap_path"]) if ghost_lap else None

# ---------------------------------------------------------------------------
# PAGE 1 — Lap Telemetry
# ---------------------------------------------------------------------------

PANEL_CFG: dict[str, tuple[int, str | None]] = {
    "Speed":            (150, "speed"),
    "Delta vs PB":      (110, None),
    "Pedals":           (130, "throttle"),
    "Steering":         (110, "steer"),
    "Gear / RPM":       (120, "gear"),
    "G-forces":         (130, "g_lon"),
    "Wheel slip":       (120, "slip_ratio_fl"),
    "Slip angle":       (120, "slip_angle_fl"),
    "Suspension":       (110, "suspension_travel_fl"),
    "Brake temps":      (120, "brake_temp_fl"),
    "Tyre state":       (120, "tyre_temp_fl"),
    "Engine thermals":  (110, "water_temp"),
    "Vibration & aids": (90,  "kerb_vibration"),
    "Fuel":             (90,  "fuel"),
}
_ALL_PANELS = list(PANEL_CFG)
_DEFAULT_PANELS = ["Speed", "Delta vs PB", "Pedals", "Steering", "Gear / RPM", "G-forces"]


def _add_panel(fig: go.Figure, row: int, name: str):
    x = gx["spline"]  # type: ignore[index]

    if name == "Speed":
        fig.add_trace(go.Scatter(
            x=x, y=gx["speed"] * 3.6, name="Speed km/h",
            line=dict(color=C["speed"], width=1.5),
            fill="tozeroy", fillcolor="rgba(236,239,241,0.06)",
        ), row=row, col=1)
        if ghost_gx is not None and "speed" in ghost_gx.columns:
            fig.add_trace(go.Scatter(
                x=ghost_gx["spline"], y=ghost_gx["speed"] * 3.6, name="Ref speed",
                line=dict(color="rgba(255,255,255,0.3)", width=1, dash="dash"),
            ), row=row, col=1)
        fig.update_yaxes(title_text="km/h", row=row, col=1)

    elif name == "Delta vs PB":
        if delta_df is not None:
            d = delta_df["delta"]
            fig.add_trace(go.Scatter(
                x=delta_df["spline"], y=d.where(d > 0),
                name="Slower", line=dict(color=C["delta_pos"], width=1),
                fill="tozeroy", fillcolor="rgba(255,23,68,0.22)",
            ), row=row, col=1)
            fig.add_trace(go.Scatter(
                x=delta_df["spline"], y=d.where(d <= 0),
                name="Faster", line=dict(color=C["delta_neg"], width=1),
                fill="tozeroy", fillcolor="rgba(0,200,83,0.22)",
            ), row=row, col=1)
            fig.add_hline(y=0, line_dash="solid", line_color="#444", line_width=1,
                          row=row, col=1)
        fig.update_yaxes(title_text="Δ s", row=row, col=1)

    elif name == "Pedals":
        fig.add_trace(go.Scatter(
            x=x, y=gx["throttle"] * 100, name="Throttle %",
            line=dict(color=C["throttle"], width=1),
            fill="tozeroy", fillcolor="rgba(0,200,83,0.18)",
        ), row=row, col=1)
        fig.add_trace(go.Scatter(
            x=x, y=gx["brake"] * 100, name="Brake %",
            line=dict(color=C["brake"], width=1),
            fill="tozeroy", fillcolor="rgba(255,23,68,0.18)",
        ), row=row, col=1)
        if _has(gx, "clutch"):
            fig.add_trace(go.Scatter(
                x=x, y=gx["clutch"] * 100, name="Clutch %",  # type: ignore[index]
                line=dict(color=C["clutch"], width=1, dash="dot"),
            ), row=row, col=1)
        fig.update_yaxes(range=[0, 105], title_text="%", row=row, col=1)

    elif name == "Steering":
        steer = gx["steer"]
        fig.add_trace(go.Scatter(
            x=x, y=steer.where(steer < 0), name="Left",
            line=dict(color=C["steer_l"], width=1),
            fill="tozeroy", fillcolor="rgba(68,138,255,0.2)",
        ), row=row, col=1)
        fig.add_trace(go.Scatter(
            x=x, y=steer.where(steer > 0), name="Right",
            line=dict(color=C["steer_r"], width=1),
            fill="tozeroy", fillcolor="rgba(255,109,0,0.2)",
        ), row=row, col=1)
        fig.update_yaxes(range=[-1.1, 1.1], title_text="steer", row=row, col=1)
        if _has(gx, "yaw_rate"):
            fig.add_trace(go.Scatter(
                x=x, y=gx["yaw_rate"], name="Yaw rate",  # type: ignore[index]
                line=dict(color=C["yaw"], width=1, dash="dot"), opacity=0.7,
            ), row=row, col=1, secondary_y=True)
            fig.update_yaxes(title_text="rad/s", secondary_y=True, row=row, col=1)

    elif name == "Gear / RPM":
        if _has(gx, "rpm"):
            fig.add_trace(go.Scatter(
                x=x, y=gx["rpm"], name="RPM",  # type: ignore[index]
                line=dict(color=C["rpm"], width=1.5),
            ), row=row, col=1)
            if max_rpm > 0:
                fig.add_hrect(y0=max_rpm * 0.95, y1=max_rpm * 1.05,
                              fillcolor="rgba(255,23,68,0.07)", line_width=0,
                              row=row, col=1)
            fig.update_yaxes(title_text="RPM", row=row, col=1)
        if _has(gx, "gear"):
            fig.add_trace(go.Scatter(
                x=x, y=gx["gear"], name="Gear",  # type: ignore[index]
                line=dict(color=C["gear"], width=2, shape="hv"),
            ), row=row, col=1, secondary_y=True)
            fig.update_yaxes(title_text="Gear", dtick=1, secondary_y=True, row=row, col=1)

    elif name == "G-forces":
        if _has(gx, "g_lon"):
            fig.add_trace(go.Scatter(
                x=x, y=gx["g_lon"], name="G-lon",  # type: ignore[index]
                line=dict(color=C["g_lon"], width=1),
                fill="tozeroy", fillcolor="rgba(0,188,212,0.12)",
            ), row=row, col=1)
        if _has(gx, "g_lat"):
            fig.add_trace(go.Scatter(
                x=x, y=gx["g_lat"], name="G-lat",  # type: ignore[index]
                line=dict(color=C["g_lat"], width=1.5),
            ), row=row, col=1)
        if _has(gx, "g_vert"):
            fig.add_trace(go.Scatter(
                x=x, y=gx["g_vert"], name="G-vert",  # type: ignore[index]
                line=dict(color=C["g_vert"], width=1),
            ), row=row, col=1)
            fig.add_hline(y=1.0, line_dash="dot", line_color="#555", line_width=1,
                          row=row, col=1)
        fig.update_yaxes(title_text="g", row=row, col=1)

    elif name == "Wheel slip":
        for lbl, color in zip(WHEEL_LABELS, WHEEL_COLORS):
            cn = f"slip_ratio_{lbl}"
            if _has(gx, cn):
                fig.add_trace(go.Scatter(
                    x=x, y=gx[cn], name=lbl.upper(), line=dict(color=color, width=1),  # type: ignore[index]
                ), row=row, col=1)
        fig.update_yaxes(title_text="slip", row=row, col=1)

    elif name == "Slip angle":
        for lbl, color in zip(WHEEL_LABELS, WHEEL_COLORS):
            cn = f"slip_angle_{lbl}"
            if _has(gx, cn):
                fig.add_trace(go.Scatter(
                    x=x, y=np.degrees(gx[cn]), name=lbl.upper(),  # type: ignore[index]
                    line=dict(color=color, width=1),
                ), row=row, col=1)
        fig.update_yaxes(title_text="deg", row=row, col=1)

    elif name == "Suspension":
        for lbl, color in zip(WHEEL_LABELS, WHEEL_COLORS):
            cn = f"suspension_travel_{lbl}"
            if _has(gx, cn):
                fig.add_trace(go.Scatter(
                    x=x, y=gx[cn] * 1000, name=lbl.upper(),  # type: ignore[index]
                    line=dict(color=color, width=1),
                ), row=row, col=1)
        fig.add_hline(y=0, line_dash="dot", line_color="#555", line_width=1, row=row, col=1)
        fig.update_yaxes(title_text="mm", row=row, col=1)

    elif name == "Brake temps":
        for lbl, color in zip(WHEEL_LABELS, WHEEL_COLORS):
            cn = f"brake_temp_{lbl}"
            if _has(gx, cn):
                fig.add_trace(go.Scatter(
                    x=x, y=gx[cn], name=f"BT {lbl.upper()}",  # type: ignore[index]
                    line=dict(color=color, width=1.5),
                ), row=row, col=1)
        fig.add_hrect(y0=300, y1=800, fillcolor="rgba(0,200,83,0.05)",
                      line_width=0, row=row, col=1)
        fig.update_yaxes(title_text="°C", row=row, col=1)
        for lbl, color in zip(WHEEL_LABELS, WHEEL_COLORS):
            cn = f"brake_pressure_{lbl}"
            if _has(gx, cn):
                fig.add_trace(go.Scatter(
                    x=x, y=gx[cn], name=f"BP {lbl.upper()}",  # type: ignore[index]
                    line=dict(color=color, width=1, dash="dot"), opacity=0.55,
                ), row=row, col=1, secondary_y=True)
        fig.update_yaxes(title_text="press", secondary_y=True, row=row, col=1)

    elif name == "Tyre state":
        for lbl, color in zip(WHEEL_LABELS, WHEEL_COLORS):
            cn = f"tyre_temp_{lbl}"
            if _has(gx, cn):
                fig.add_trace(go.Scatter(
                    x=x, y=gx[cn], name=f"TT {lbl.upper()}",  # type: ignore[index]
                    line=dict(color=color, width=1.5),
                ), row=row, col=1)
        fig.update_yaxes(title_text="°C", row=row, col=1)
        for lbl, color in zip(WHEEL_LABELS, WHEEL_COLORS):
            cn = f"tyre_press_{lbl}"
            if _has(gx, cn):
                fig.add_trace(go.Scatter(
                    x=x, y=gx[cn], name=f"TP {lbl.upper()}",  # type: ignore[index]
                    line=dict(color=color, width=1, dash="dot"), opacity=0.65,
                ), row=row, col=1, secondary_y=True)
        fig.update_yaxes(title_text="kPa", secondary_y=True, row=row, col=1)

    elif name == "Engine thermals":
        if _has(gx, "water_temp"):
            fig.add_trace(go.Scatter(
                x=x, y=gx["water_temp"], name="Water °C",  # type: ignore[index]
                line=dict(color=C["water_t"], width=1.5),
            ), row=row, col=1)
            fig.add_hline(y=105, line_dash="dash", line_color=C["brake"], line_width=1,
                          row=row, col=1)
        if _has(gx, "exhaust_temp"):
            fig.add_trace(go.Scatter(
                x=x, y=gx["exhaust_temp"], name="Exhaust °C",  # type: ignore[index]
                line=dict(color=C["exhaust"], width=1),
            ), row=row, col=1)
        if _has(gx, "turbo_boost"):
            fig.add_trace(go.Scatter(
                x=x, y=gx["turbo_boost"], name="Boost bar",  # type: ignore[index]
                line=dict(color=C["boost"], width=1),
            ), row=row, col=1, secondary_y=True)
            fig.update_yaxes(title_text="bar", secondary_y=True, row=row, col=1)
        fig.update_yaxes(title_text="°C", row=row, col=1)

    elif name == "Vibration & aids":
        if _has(gx, "kerb_vibration"):
            fig.add_trace(go.Scatter(
                x=x, y=gx["kerb_vibration"], name="Kerb vib",  # type: ignore[index]
                line=dict(color=C["kerb_vib"], width=1),
                fill="tozeroy", fillcolor="rgba(255,143,0,0.15)",
            ), row=row, col=1)
        if _has(gx, "slip_vibration"):
            fig.add_trace(go.Scatter(
                x=x, y=gx["slip_vibration"], name="Slip vib",  # type: ignore[index]
                line=dict(color=C["slip_vib"], width=1),
                fill="tozeroy", fillcolor="rgba(233,30,99,0.15)",
            ), row=row, col=1)
        for col_n, lbl, rgba in [
            ("abs_active", "ABS", "rgba(255,23,68,0.28)"),
            ("tc_active",  "TC",  "rgba(0,200,83,0.28)"),
        ]:
            if _has(gx, col_n):
                fig.add_trace(go.Scatter(
                    x=x, y=gx[col_n] * 0.25, name=lbl,  # type: ignore[index]
                    fill="tozeroy", line=dict(width=0), fillcolor=rgba,
                ), row=row, col=1)
        fig.update_yaxes(title_text="mag", row=row, col=1)

    elif name == "Fuel":
        if _has(gx, "fuel"):
            fig.add_trace(go.Scatter(
                x=x, y=gx["fuel"], name="Fuel L",  # type: ignore[index]
                line=dict(color=C["fuel"], width=1.5),
                fill="tozeroy", fillcolor="rgba(128,203,196,0.18)",
            ), row=row, col=1)
            if max_fuel > 0:
                fig.update_yaxes(range=[0, max_fuel * 1.05], row=row, col=1)
        fig.update_yaxes(title_text="L", row=row, col=1)


def _page_telemetry():
    # KPI strip
    c1, c2, c3, c4, c5, c6 = st.columns(6)
    m, s = divmod(sel_lap["lap_time"], 60)
    c1.metric("Lap time", f"{int(m)}:{s:06.3f}", help="✓ Valid" if sel_lap["valid"] else "✗ Invalid")

    if delta_df is not None and len(delta_df):
        fd = float(delta_df["delta"].iloc[-1])
        c2.metric("Total Δ vs PB", f"{fd:+.3f}s", delta=f"{fd:.3f}", delta_color="inverse")
    else:
        c2.metric("Total Δ vs PB", "No ref lap")

    if gx is not None and "speed" in gx.columns:
        c3.metric("Peak speed", f"{gx['speed'].max()*3.6:.0f} km/h")
        c4.metric("Min speed",  f"{gx['speed'].min()*3.6:.0f} km/h")
    else:
        c3.metric("Peak speed", "—"); c4.metric("Min speed", "—")

    glm = extras.get("g_lat_max") or (_has(gx, "g_lat") and float(gx["g_lat"].abs().max()))  # type: ignore[index]
    c5.metric("Max g-lat", f"{glm:.2f} g" if glm else "—")

    fu = extras.get("fuel_used_lap")
    if fu is None and _has(gx, "fuel"):
        fu = float(gx["fuel"].iloc[0] - gx["fuel"].iloc[-1])  # type: ignore[index]
    c6.metric("Fuel used", f"{fu:.2f} L" if fu is not None else "—")

    if gx is None:
        st.warning("Telemetry not available for this lap.")
        return

    active = st.multiselect("Panels", _ALL_PANELS, default=_DEFAULT_PANELS)
    if not active:
        st.info("Select at least one panel.")
        return

    # Filter unavailable panels
    panel_list: list[tuple[str, int]] = []
    skipped: list[str] = []
    for name in active:
        height, key_col = PANEL_CFG[name]
        if name == "Delta vs PB" and delta_df is None:
            skipped.append(name); continue
        if key_col is not None and not _has(gx, key_col):
            skipped.append(name); continue
        panel_list.append((name, height))

    if skipped:
        st.caption(f"⚠ Not in this lap's data: {', '.join(skipped)}")
    if not panel_list:
        st.info("No data for selected panels.")
        return

    # Findings strip as row 0
    input_f = [f for f in findings if f["kind"] != "sector_delta"]
    has_strip = bool(input_f)
    strip_h = 70
    n = len(panel_list) + (1 if has_strip else 0)
    heights = ([strip_h] if has_strip else []) + [h for _, h in panel_list]
    total_h = sum(heights) + 60

    fig = make_subplots(
        rows=n, cols=1, shared_xaxes=True, vertical_spacing=0.012,
        row_heights=heights,
        specs=[[{"secondary_y": True}] for _ in range(n)],
    )

    row_offset = 0
    if has_strip:
        row_offset = 1
        by_kind: dict[str, list] = defaultdict(list)
        for f in input_f:
            detail = f.get("detail") or {}
            sp_pos = detail.get("spline") or detail.get("spline_start")
            if sp_pos is None:
                continue
            lbl = KIND_LABEL.get(f["kind"], f["kind"])
            by_kind[lbl].append((float(sp_pos), max(8, float(f["severity"]) * 20),
                                  detail.get("fix", ""), round(float(f["severity"]), 2)))
        for lbl, pts in by_kind.items():
            color = KIND_COLOR.get(
                next((k for k, v in KIND_LABEL.items() if v == lbl), ""), "#aaa")
            fig.add_trace(go.Scatter(
                x=[p[0] for p in pts], y=[lbl] * len(pts), mode="markers",
                name=lbl, showlegend=False,
                marker=dict(size=[p[1] for p in pts], color=color, opacity=0.9,
                            line=dict(width=1, color="rgba(255,255,255,0.2)")),
                hovertemplate="<b>%{y}</b>  pos %{x:.3f}<br>%{text}<extra></extra>",
                text=[p[2] for p in pts],
            ), row=1, col=1)
        fig.update_yaxes(title_text="", row=1, col=1)

    for i, (name, _) in enumerate(panel_list, start=1 + row_offset):
        _add_panel(fig, i, name)

    # Shared x-axis config
    for r in range(1, n + 1):
        fig.update_xaxes(range=[0, 1], showspikes=True, spikemode="across",
                         spikesnap="cursor", spikethickness=1,
                         spikecolor=C["grid_line"], row=r, col=1)
    fig.update_xaxes(title_text="Track position (spline 0→1)", row=n, col=1)

    _sector_lines(fig)
    fig.update_layout(
        height=total_h, template="plotly_dark",
        paper_bgcolor=C["bg"], plot_bgcolor=C["panel"],
        hovermode="x unified",
        margin=dict(l=70, r=20, t=20, b=30),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, x=0, font_size=10),
    )
    st.plotly_chart(fig, use_container_width=True, config={"scrollZoom": True})

# ---------------------------------------------------------------------------
# PAGE 2 — Track Map
# ---------------------------------------------------------------------------

def _page_map():
    col_map, col_ctrl = st.columns([3, 1])

    channel_opts = ["speed (km/h)", "throttle", "brake", "g_lat", "g_lon", "delta"]
    if gx is not None:
        for base in ["brake_temp", "slip_ratio", "tyre_temp"]:
            if _has(gx, f"{base}_fl"):
                channel_opts.append(f"{base}_fl")

    with col_ctrl:
        st.subheader("Controls")
        channel = st.selectbox("Colour by", channel_opts)
        use_coords = st.checkbox("Use GPS coords", value=coords_df is not None,
                                  disabled=coords_df is None)
        if coords_df is None:
            st.caption("No GPS coords captured (CAPTURE_COORDS not set).")

    colorscale_map = {
        "speed (km/h)": "Viridis", "throttle": "Greens", "brake": "Reds",
        "g_lat": "RdBu", "g_lon": "RdYlGn", "delta": "RdYlGn_r",
    }
    colorscale = colorscale_map.get(channel, "Plasma")

    with col_map:
        if use_coords and coords_df is not None and gx is not None:
            # Merge channel values onto coords via nearest spline
            ch_col = channel.replace(" (km/h)", "")
            if ch_col == "speed":
                vals = gx["speed"] * 3.6
                src_spline = gx["spline"]
            elif ch_col in gx.columns:
                vals = gx[ch_col]
                src_spline = gx["spline"]
            elif channel == "delta" and delta_df is not None:
                vals = delta_df["delta"]
                src_spline = delta_df["spline"]
            else:
                vals = pd.Series(np.zeros(len(gx)))
                src_spline = gx["spline"]

            # Nearest-spline join
            if "spline" in coords_df.columns:
                coords_spline = coords_df["spline"].to_numpy()
                src_sp = src_spline.to_numpy()
                idx = np.searchsorted(src_sp, coords_spline).clip(0, len(src_sp) - 1)
                color_vals = vals.to_numpy()[idx]
                cx, cz = coords_df["x"].to_numpy(), coords_df["z"].to_numpy()
            else:
                cx, cz = coords_df.get("x", pd.Series()).to_numpy(), \
                         coords_df.get("z", coords_df.get("y", pd.Series())).to_numpy()
                color_vals = np.zeros(len(cx))

            fig_map = go.Figure(go.Scattergl(
                x=cx, y=cz, mode="markers",
                marker=dict(size=3, color=color_vals, colorscale=colorscale, showscale=True,
                            colorbar=dict(title=channel, thickness=12)),
                hovertemplate=f"{channel}: %{{marker.color:.2f}}<extra></extra>",
            ))
            # Sector markers
            if gx is not None and "spline" in coords_df.columns:
                sp_arr = coords_df["spline"].to_numpy()
                for sec in range(1, 20):
                    tgt = sec / 20
                    ii  = np.argmin(np.abs(sp_arr - tgt))
                    fig_map.add_annotation(x=float(cx[ii]), y=float(cz[ii]),
                                           text=str(sec), showarrow=False,
                                           font=dict(size=9, color="#aaa"))
            fig_map.update_layout(
                template="plotly_dark", paper_bgcolor=C["bg"], plot_bgcolor=C["panel"],
                height=620, margin=dict(l=10, r=10, t=30, b=10),
                xaxis=dict(scaleanchor="y", showticklabels=False),
                yaxis=dict(showticklabels=False),
                title=f"Track map — coloured by {channel}",
            )

        else:
            # Linear ribbon fallback
            if gx is None:
                st.warning("No telemetry."); return
            ch_col = channel.replace(" (km/h)", "")
            if ch_col == "speed":
                y_vals = gx["speed"] * 3.6
            elif ch_col in gx.columns:
                y_vals = gx[ch_col]
            elif channel == "delta" and delta_df is not None:
                y_vals = delta_df["delta"]
            else:
                y_vals = pd.Series(np.zeros(GRID_N))

            fig_map = go.Figure(go.Scatter(
                x=gx["spline"], y=y_vals, mode="lines",
                line=dict(color=C["speed"], width=2),
                fill="tozeroy", fillcolor="rgba(236,239,241,0.08)",
                name=channel,
            ))
            _sector_lines(fig_map)
            fig_map.update_layout(
                template="plotly_dark", paper_bgcolor=C["bg"], plot_bgcolor=C["panel"],
                height=400, margin=dict(l=60, r=20, t=30, b=40),
                xaxis_title="Track position", yaxis_title=channel,
                title=f"Linear ribbon — {channel} (no GPS captured)",
            )

        st.plotly_chart(fig_map, use_container_width=True)

# ---------------------------------------------------------------------------
# PAGE 3 — Session & Health
# ---------------------------------------------------------------------------

def _chip(label: str, color: str = "#333"):
    st.markdown(
        f"<span style='background:{color};padding:2px 8px;border-radius:4px;"
        f"font-size:12px;color:#fff'>{label}</span>",
        unsafe_allow_html=True,
    )


def _page_health():
    # Row A — Session header
    st.subheader("Session")
    ca1, ca2, ca3, ca4, ca5 = st.columns(5)
    ca1.metric("Player",   sel_sess.get("player_name") or "—")
    ca2.metric("Car",      sel_sess["car"])
    ca3.metric("Track",    sel_sess["track"])
    ca4.metric("Type",     sel_sess["session_type"])
    with ca5:
        is_onl = sel_sess.get("is_online") in (True, "True", "true", "1")
        _chip("Online" if is_onl else "Offline", "#00C853" if is_onl else "#555")
        if sel_sess.get("sector_count"):
            st.caption(f"{sel_sess['sector_count']} sectors")
        if sel_sess.get("penalty_enabled") in (True, "True", "true", "1"):
            _chip("Penalties ON", "#FFB300")

    st.divider()

    # Row B — Aids
    st.subheader("Aid settings")
    cb1, cb2, cb3, cb4, cb5, cb6 = st.columns(6)
    def _aid_bar(col, label: str, key: str, max_v: float = 2.0):
        val = sel_sess.get(key)
        try:   val = float(val)
        except: val = None
        if val is not None:
            col.metric(label, f"{val:.1f} / {max_v:.0f}")
            col.progress(min(val / max_v, 1.0))
        else:
            col.metric(label, "—")
    _aid_bar(cb1, "Fuel rate",    "aid_fuel_rate")
    _aid_bar(cb2, "Tyre rate",    "aid_tyre_rate")
    _aid_bar(cb3, "Mech dmg",     "aid_mechanical_damage")
    _aid_bar(cb4, "Stability",    "aid_stability")
    with cb5:
        for k, lbl in [("aid_auto_clutch", "Auto clutch"), ("penalty_enabled", "Penalties")]:
            v = sel_sess.get(k)
            if v in (True, "True", "true", "1"):
                _chip(lbl, "#00C853")
            elif v is not None:
                _chip(f"No {lbl}", "#555")

    tc  = extras.get("tc_level")  or (_has(gx, "tc_level")  and int(gx["tc_level"].iloc[-1]))   # type: ignore[index]
    ab  = extras.get("abs_level") or (_has(gx, "abs_level") and int(gx["abs_level"].iloc[-1]))   # type: ignore[index]
    em  = extras.get("engine_map")or (_has(gx, "engine_map")and int(gx["engine_map"].iloc[-1]))  # type: ignore[index]
    with cb6:
        if tc  is not None: st.caption(f"TC: {tc}")
        if ab  is not None: st.caption(f"ABS: {ab}")
        if em  is not None: st.caption(f"Map: {em}")

    st.divider()

    # Row C — Sector loss + ACC delta cross-check
    st.subheader("Sector time loss")
    sector_f = [f for f in findings if f["kind"] == "sector_delta"]
    if sector_f:
        rows = []
        for f in sector_f:
            d = f.get("detail") or {}
            rows.append({"Sector": f["corner"],
                         "Spline": f"{d.get('spline_start',0):.2f}–{d.get('spline_end',0):.2f}",
                         "Δ s": f"{f['time_loss_s']:+.3f}", "Sev": round(f["severity"], 2)})
        def _clr(v):
            try:
                f_v = float(v)
                if f_v > 0.05:  return "color:#FF1744"
                if f_v < -0.05: return "color:#00C853"
            except: pass
            return ""
        st.dataframe(pd.DataFrame(rows).style.map(_clr, subset=["Δ s"]),
                     use_container_width=True, hide_index=True)

    if delta_df is not None and extras.get("delta_lap_time_end") is not None:
        computed_ms = float(delta_df["delta"].iloc[-1]) * 1000
        acc_ms      = float(extras["delta_lap_time_end"])
        diff_ms     = abs(computed_ms - acc_ms)
        st.caption(f"Pipeline Δ: {computed_ms:+.0f} ms  ·  ACC Δ: {acc_ms:+.0f} ms  ·  "
                   f"Diff: {diff_ms:.0f} ms {'✓' if diff_ms < 200 else '⚠'}")

    st.divider()

    # Row D — G-G friction circle + peak dynamics
    st.subheader("Grip & dynamics")
    cd1, cd2 = st.columns([2, 1])

    with cd1:
        if _has(gx, "g_lat") and _has(gx, "g_lon"):
            spd = gx["speed"] * 3.6 if _has(gx, "speed") else None  # type: ignore[index]
            fig_gg = go.Figure()
            fig_gg.add_trace(go.Scattergl(
                x=gx["g_lat"], y=gx["g_lon"],  # type: ignore[index]
                mode="markers",
                marker=dict(size=2, color=spd, colorscale="Viridis",
                            colorbar=dict(title="km/h", thickness=10) if spd is not None else {}),
                name="G-G",
            ))
            theta = np.linspace(0, 2 * np.pi, 120)
            fig_gg.add_trace(go.Scatter(
                x=1.5 * np.cos(theta), y=1.5 * np.sin(theta),
                mode="lines", line=dict(color="#555", dash="dash"), name="1.5g ref",
            ))
            fig_gg.update_layout(
                template="plotly_dark", paper_bgcolor=C["bg"], plot_bgcolor=C["panel"],
                height=320, margin=dict(l=50, r=10, t=20, b=40),
                xaxis_title="G-lat (right+)", yaxis_title="G-lon (accel+)",
                xaxis=dict(range=[-2, 2], scaleanchor="y"),
                yaxis=dict(range=[-2.5, 2.5]),
            )
            st.plotly_chart(fig_gg, use_container_width=True)
        else:
            st.caption("⚠ G-force data not in this lap.")

    with cd2:
        peak_metrics = [
            ("g_lat_max",          "g_lat_max",       "{:.2f} g"),
            ("g_lon_max",          "g_lon_max",       "{:.2f} g"),
            ("g_lon_min",          "g_lon_min",       "{:.2f} g"),
            ("g_vert_max",         "g_vert_max",      "{:.2f} g"),
            ("yaw_rate_abs_max",   "yaw_rate_abs_max","{:.2f} r/s"),
            ("local_vel_x_abs_max","local_vel_x_abs_max","{:.2f} m/s"),
            ("slip_angle_abs_max", "slip_angle_abs_max","{:.2f}"),
            ("slip_ratio_abs_max", "slip_ratio_abs_max","{:.3f}"),
            ("wheel_slip_max",     "wheel_slip_max",  "{:.3f}"),
            ("susp_travel_min",    "susp_travel_min", "{:.3f} m"),
        ]
        for label, key, fmt in peak_metrics:
            val = extras.get(key)
            st.metric(label, fmt.format(val) if val is not None else "—")

    st.divider()

    # Row E — Brakes & wear
    st.subheader("Brakes & wear")
    ce1, ce2, ce3, ce4 = st.columns(4)
    bt_max = extras.get("brake_temp_max")
    if bt_max is not None:
        fig_btg = go.Figure(go.Indicator(
            mode="gauge+number", value=float(bt_max),
            gauge=dict(axis=dict(range=[0, 1000]),
                       bar=dict(color=C["brake"]),
                       steps=[dict(range=[300, 800], color="rgba(0,200,83,0.15)")],
                       threshold=dict(line=dict(color="red", width=2), value=800)),
            title=dict(text="Brake temp max °C"),
            domain=dict(x=[0,1], y=[0,1]),
        ))
        fig_btg.update_layout(height=200, margin=dict(l=20,r=20,t=40,b=10),
                               template="plotly_dark", paper_bgcolor=C["bg"])
        ce1.plotly_chart(fig_btg, use_container_width=True)
    else:
        ce1.metric("Brake temp max", "—")

    ce2.metric("Brake press max", f"{extras.get('brake_press_max', '—')}")
    bbm = extras.get("brake_bias_mean")
    ce3.metric("Brake bias mean", f"{float(bbm)*100:.1f}%" if bbm is not None else "—")
    pl  = extras.get("pad_life_min");  dl = extras.get("disc_life_min")
    ce4.metric("Pad life min",  f"{float(pl)*100:.0f}%" if pl  is not None else "—",
               delta="worn" if pl is not None and float(pl) < 0.2 else None,
               delta_color="inverse")
    ce4.metric("Disc life min", f"{float(dl)*100:.0f}%" if dl is not None else "—",
               delta="worn" if dl is not None and float(dl) < 0.1 else None,
               delta_color="inverse")

    st.divider()

    # Row F — Damage
    st.subheader("Damage")
    cf1, cf2 = st.columns(2)
    cd_total  = extras.get("car_damage_total")
    sd_total  = extras.get("susp_damage_total")
    cf1.metric("Car damage total",  f"{cd_total:.3f}" if cd_total  else "0.000")
    cf2.metric("Susp damage total", f"{sd_total:.3f}" if sd_total else "0.000")

    if gx is not None and any(_has(gx, f"car_damage_{l}") for l in _CAR_DMG_LABELS):
        dmg_vals = [float(gx[f"car_damage_{l}"].iloc[-1]) if _has(gx, f"car_damage_{l}") else 0  # type: ignore[index]
                    for l in _CAR_DMG_LABELS]
        fig_dmg = go.Figure(go.Bar(
            x=["FL", "FR", "RL", "RR", "Center"], y=dmg_vals,
            marker_color=[C["brake"] if v > 0 else "#333" for v in dmg_vals],
        ))
        _base_layout(fig_dmg, 180)
        fig_dmg.update_layout(margin=dict(l=40,r=10,t=20,b=30), showlegend=False)
        st.plotly_chart(fig_dmg, use_container_width=True)

    if gx is not None and any(_has(gx, f"suspension_damage_{l}") for l in WHEEL_LABELS):
        sd_vals = [float(gx[f"suspension_damage_{l}"].iloc[-1]) if _has(gx, f"suspension_damage_{l}") else 0  # type: ignore[index]
                   for l in WHEEL_LABELS]
        fig_sd = go.Figure(go.Bar(
            x=WHEEL_DISPLAY, y=sd_vals,
            marker_color=[C["g_lat"] if v > 0 else "#333" for v in sd_vals],
        ))
        _base_layout(fig_sd, 160)
        fig_sd.update_layout(margin=dict(l=40,r=10,t=10,b=30), showlegend=False)
        st.plotly_chart(fig_sd, use_container_width=True)

    st.divider()

    # Row G — Engine
    st.subheader("Engine")
    cg1, cg2, cg3, cg4 = st.columns(4)
    cg1.metric("Water temp mean", f"{extras.get('water_temp_mean','—')}")
    wtm = extras.get("water_temp_max")
    cg2.metric("Water temp max",  f"{wtm}°C" if wtm else "—",
               delta="⚠ hot" if wtm and float(wtm) > 105 else None, delta_color="inverse")
    cg3.metric("Turbo boost max", f"{extras.get('turbo_boost_max','—')} bar")
    cg4.metric("Exhaust max",     f"{extras.get('exhaust_temp_max','—')}°C")

    with cg4:
        for k, lbl in [("autoshifter_on","Auto shift"),("pit_limiter_on","Pit limiter"),("is_ai_controlled","AI")]:
            if _has(gx, k) and gx[k].max() > 0.5:  # type: ignore[index]
                _chip(lbl, "#FFB300")

    st.divider()

    # Row H — Race state & surface
    st.subheader("Race state & surface")
    ch1, ch2, ch3, ch4 = st.columns(4)

    gs = extras.get("track_grip_status")
    if gs is not None:
        with ch1:
            _chip(GRIP_LABEL.get(int(gs), str(gs)), GRIP_COLOR.get(int(gs), "#555"))
    ch1.metric("Kerb vib max", f"{extras.get('kerb_vibration_max','—')}")
    ch1.metric("Slip vib max", f"{extras.get('slip_vibration_max','—')}")

    fs = extras.get("flag_seen")
    if fs:
        ch2.markdown(FLAG_LABEL.get(int(fs), str(fs)))
    ch2.metric("Pit stops",    f"{extras.get('pit_sample_count', 0)}")
    ch2.metric("Fuel/lap est", f"{extras.get('fuel_per_lap','—')} L" if extras.get('fuel_per_lap') else "—")

    if _has(gx, "position"):
        pos = int(gx["position"].iloc[-1])  # type: ignore[index]
        ch3.metric("Position", f"P{pos}")
    if _has(gx, "gap_ahead"):
        ch3.metric("Gap ahead", f"{gx['gap_ahead'].iloc[-1]:.2f}s")  # type: ignore[index]
    if _has(gx, "gap_behind"):
        ch3.metric("Gap behind", f"{gx['gap_behind'].iloc[-1]:.2f}s")  # type: ignore[index]

    pt = extras.get("penalty_time")
    if pt and float(pt) > 0:
        ch4.metric("Penalty", f"{float(pt):.1f}s", delta="⚠", delta_color="inverse")
    for k in ("global_yellow_s1","global_yellow_s2","global_yellow_s3"):
        if _has(gx, k) and gx[k].max() > 0.5:  # type: ignore[index]
            with ch4: _chip(f"Yellow {k[-2:].upper()}", "#FFB300")

    st.divider()

    # Row I — Weather
    st.subheader("Weather")
    ci1, ci2, ci3, ci4, ci5 = st.columns(5)
    ci1.metric("Air temp",  f"{extras.get('air_temp_mean','—')}°C")
    ci2.metric("Road temp", f"{extras.get('road_temp_mean','—')}°C")

    if _has(gx, "wind_speed"):
        ci3.metric("Wind speed", f"{gx['wind_speed'].mean():.1f} m/s")  # type: ignore[index]

    if _has(gx, "wind_direction"):
        wd  = float(gx["wind_direction"].mean())  # type: ignore[index]
        fig_wind = go.Figure(go.Barpolar(r=[1], theta=[wd], width=[30],
                                          marker_color=C["g_lon"]))
        fig_wind.update_layout(polar=dict(angularaxis=dict(direction="clockwise", rotation=90)),
                                height=150, margin=dict(l=10,r=10,t=10,b=10),
                                template="plotly_dark", paper_bgcolor=C["bg"],
                                showlegend=False)
        ci3.plotly_chart(fig_wind, use_container_width=True)

    if _has(gx, "rain_10min"):
        r10 = int(gx["rain_10min"].iloc[-1])  # type: ignore[index]
        with ci4: _chip(f"10min: {RAIN_LABEL.get(r10, str(r10))}", "#1565C0")
    if _has(gx, "rain_30min"):
        r30 = int(gx["rain_30min"].iloc[-1])  # type: ignore[index]
        with ci5: _chip(f"30min: {RAIN_LABEL.get(r30, str(r30))}", "#1565C0")

    if _has(gx, "driver_stint_time_left"):
        ms  = int(gx["driver_stint_time_left"].iloc[-1])  # type: ignore[index]
        if ms > 0:
            mins, secs = divmod(ms // 1000, 60)
            ci5.metric("Stint left", f"{mins}:{secs:02d}")

    st.divider()

    # Row J — Coaching notes
    st.subheader("Coaching notes")
    input_f = [f for f in findings if f["kind"] != "sector_delta"]
    if not input_f:
        st.caption("No input coaching findings for this lap.")
    else:
        rows = []
        for f in sorted(input_f, key=lambda x: -x["severity"]):
            d = f.get("detail") or {}
            rows.append({
                "Sector": f["corner"],
                "Pos":    round(d.get("spline") or d.get("spline_start") or 0.0, 3),
                "Finding": KIND_LABEL.get(f["kind"], f["kind"]),
                "Sev":    round(f["severity"], 2),
                "Fix":    d.get("fix", ""),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

if page == "① Lap Telemetry":
    _page_telemetry()
elif page == "② Track Map":
    _page_map()
else:
    _page_health()
