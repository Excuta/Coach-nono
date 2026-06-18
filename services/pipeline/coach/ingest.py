"""
Ingest worker — runs inside Docker, watches data/raw/ for completed laps.

Polls raw/ every 2 s for *.meta.json files written by the capture agent.
For each new file:
  1. Validates the parquet (readable, non-empty).
  2. Inserts a sessions row (upsert on capture_id) and a laps row (status=pending).
  3. Moves the parquet to laps/<session_id>/ and deletes the meta.json.
  4. (If extended parquet) computes per-lap aggregates and inserts into extras.
  5. (If coords parquet present) registers the path in the coordinates table.

Idempotent: ON CONFLICT DO NOTHING on laps/extras/coordinates; re-runs after a crash are safe.
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

import numpy as np
import pandas as pd

from coach.config import cfg
from coach.db import (
    get_or_create_session,
    insert_coords,
    insert_extras,
    insert_lap,
    lap_id as make_lap_id,
)

logging.basicConfig(
    level=cfg.log_level,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ingest")

POLL_INTERVAL = 2.0   # seconds between raw/ scans
MIN_SAMPLES = 100     # reject obviously empty/corrupt parquets


# ---------------------------------------------------------------------------
# Extras computation
# ---------------------------------------------------------------------------

def _extract_extras(df: pd.DataFrame) -> dict | None:
    """Compute per-lap aggregates from an extended-format parquet.

    Returns None if the parquet predates the extended schema (no 'g_lat' column).
    """
    if "g_lat" not in df.columns:
        return None

    def _col(col: str, fn) -> float | None:
        if col not in df.columns:
            return None
        arr = df[col].to_numpy(dtype=float)
        try:
            v = fn(arr)
            return float(v) if np.isfinite(v) else None
        except Exception:
            return None

    def _wmax(col: str) -> float | None:
        """Max absolute value across all wheels (object column of 4-tuples/lists)."""
        if col not in df.columns:
            return None
        try:
            arr = np.array([list(x) for x in df[col].dropna()], dtype=float)
            return float(np.nanmax(np.abs(arr))) if arr.size else None
        except Exception:
            return None

    def _wmin(col: str) -> float | None:
        """Min value across all wheels."""
        if col not in df.columns:
            return None
        try:
            arr = np.array([list(x) for x in df[col].dropna()], dtype=float)
            return float(np.nanmin(arr)) if arr.size else None
        except Exception:
            return None

    def _last_int(col: str) -> int | None:
        if col not in df.columns:
            return None
        try:
            v = df[col].dropna()
            return int(v.iloc[-1]) if len(v) else None
        except Exception:
            return None

    def _last_tuple_sum(col: str) -> float | None:
        if col not in df.columns:
            return None
        try:
            last = df[col].dropna().iloc[-1]
            return float(sum(float(x) for x in last))
        except Exception:
            return None

    fuel = df["fuel"].dropna().to_numpy(dtype=float) if "fuel" in df.columns else None

    result = {
        "g_lat_max":           _col("g_lat", lambda a: np.nanmax(np.abs(a))),
        "g_lon_max":           _col("g_lon", np.nanmax),
        "g_lon_min":           _col("g_lon", np.nanmin),
        "g_vert_max":          _col("g_vert", lambda a: np.nanmax(np.abs(a))),
        "yaw_rate_abs_max":    _col("yaw_rate", lambda a: np.nanmax(np.abs(a))),
        "local_vel_x_abs_max": _col("local_vel_x", lambda a: np.nanmax(np.abs(a))),
        "slip_angle_abs_max":  _wmax("slip_angle"),
        "slip_ratio_abs_max":  _wmax("slip_ratio"),
        "wheel_slip_max":      _wmax("wheel_slip"),
        "susp_travel_min":     _wmin("suspension_travel"),
        "brake_temp_max":      _wmax("brake_temp"),
        "brake_press_max":     _wmax("brake_pressure"),
        "brake_bias_mean":     _col("brake_bias", np.nanmean),
        "water_temp_mean":     _col("water_temp", np.nanmean),
        "water_temp_max":      _col("water_temp", np.nanmax),
        "turbo_boost_max":     _col("turbo_boost", np.nanmax),
        "exhaust_temp_max":    _col("exhaust_temp", np.nanmax),
        "air_temp_mean":       _col("air_temp", np.nanmean),
        "road_temp_mean":      _col("road_temp", np.nanmean),
        "pad_life_min":        _wmin("pad_life"),
        "disc_life_min":       _wmin("disc_life"),
        "car_damage_total":    _last_tuple_sum("car_damage"),
        "susp_damage_total":   _last_tuple_sum("suspension_damage"),
        "fuel_used_lap":       float(fuel[0] - fuel[-1]) if fuel is not None and len(fuel) > 1 else None,
        "kerb_vibration_max":  _col("kerb_vibration", np.nanmax),
        "slip_vibration_max":  _col("slip_vibration", np.nanmax),
        "tc_level":            _last_int("tc_level"),
        "abs_level":           _last_int("abs_level"),
        "engine_map":          _last_int("engine_map"),
        "track_grip_status":   _last_int("track_grip_status"),
        "flag_seen":           _col("flag", np.nanmax),
        "pit_sample_count":    int(df["is_in_pit"].sum()) if "is_in_pit" in df.columns else None,
        "delta_lap_time_end":  _last_int("delta_lap_time"),
    }
    # Drop None values so the INSERT only sets columns that have data
    return {k: v for k, v in result.items() if v is not None}


# ---------------------------------------------------------------------------
# Core processing
# ---------------------------------------------------------------------------

def _process(meta_path: Path) -> None:
    try:
        meta = json.loads(meta_path.read_text())
    except Exception as exc:
        log.error("Cannot parse %s: %s — skipping", meta_path, exc)
        meta_path.unlink(missing_ok=True)
        return

    capture_id: str = meta["session_id"]
    lap_index: int = meta["lap_index"]
    parquet_src = meta_path.parent / meta["parquet_file"]

    dest_dir = cfg.laps_dir / capture_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / meta["parquet_file"]

    # --- Validate before moving so a corrupt file stays in raw/ for inspection ---
    validate_src = parquet_src if parquet_src.exists() else dest_path
    if not validate_src.exists():
        log.error("Parquet missing from both raw and laps: %s", meta["parquet_file"])
        meta_path.unlink(missing_ok=True)
        return

    try:
        df = pd.read_parquet(validate_src)
    except Exception as exc:
        log.error("Cannot read parquet %s: %s", validate_src, exc)
        if validate_src == parquet_src:
            # Leave parquet + meta.json in raw/ for retry / manual inspection
            return
        # Already in laps/ (crash-recovery case) — stop retrying, clean up meta
        meta_path.unlink(missing_ok=True)
        return

    # --- Move parquet (handle already-moved case) ---
    if parquet_src.exists():
        shutil.move(str(parquet_src), str(dest_path))

    if len(df) < MIN_SAMPLES:
        log.warning("Lap %d has only %d samples — registered but may be partial", lap_index, len(df))

    # --- Register in DB ---
    db_session_id = get_or_create_session(
        capture_id=capture_id,
        game=meta["game"],
        car=meta["car"],
        track=meta["track"],
        session_type=meta["session_type"],
        conditions=meta.get("conditions", {}),
        statics=meta.get("statics"),
    )

    lid = make_lap_id(capture_id, lap_index)
    insert_lap(
        lid=lid,
        session_db_id=db_session_id,
        lap_index=lap_index,
        lap_time_s=meta["lap_time_ms"] / 1000.0,
        valid=bool(meta.get("valid", True)),
        raw_path=str(parquet_src),
        lap_path=str(dest_path),
    )

    # --- Extended telemetry aggregates (non-fatal if missing or broken) ---
    try:
        agg = _extract_extras(df)
        if agg:
            insert_extras(lid, agg)
            log.debug("Extras inserted for lap %s (%d fields)", lid, len(agg))
    except Exception:
        log.exception("Failed to insert extras for lap %s — lap row still committed", lid)

    # --- Coordinates registration ---
    coords_file = meta.get("coords_file")
    if coords_file:
        try:
            coords_path = cfg.coords_dir / capture_id / coords_file
            sample_count = len(pd.read_parquet(coords_path))
            insert_coords(lid, str(coords_path), sample_count)
            log.debug("Coords registered for lap %s (%d samples)", lid, sample_count)
        except Exception:
            log.exception("Failed to register coords for lap %s — lap row still committed", lid)

    meta_path.unlink()

    lap_s = meta["lap_time_ms"] / 1000.0
    m, s = divmod(lap_s, 60)
    log.info(
        "Ingested %s  idx=%d  %d:%06.3f  valid=%s  samples=%d",
        lid, lap_index, int(m), s,
        meta.get("valid", True),
        meta.get("sample_count", len(df)),
    )


def run() -> None:
    log.info("Ingest worker started. Watching %s", cfg.raw_dir)
    cfg.raw_dir.mkdir(parents=True, exist_ok=True)
    cfg.laps_dir.mkdir(parents=True, exist_ok=True)

    while True:
        for meta_path in sorted(cfg.raw_dir.rglob("*.meta.json")):
            try:
                _process(meta_path)
            except Exception as exc:
                log.exception("Unexpected error processing %s: %s", meta_path, exc)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
