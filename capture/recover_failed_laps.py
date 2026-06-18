"""
One-shot recovery script for laps saved as .failed.json by the capture agent.

Usage (from repo root):
    .\capture\venv\Scripts\python.exe capture\recover_failed_laps.py

For each lap_NNN.failed.json found in data/raw/*/, recreates:
  - lap_NNN.parquet  (same directory, written atomically)
  - lap_NNN.meta.json

Ingest will then pick them up normally within 2 seconds.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).parent.parent
_RAW  = _ROOT / "data" / "raw"

SESSION_STATICS = {
    "game":         "acc",
    "session_type": "practice",
    "conditions":   {"rain_intensity": 0},
    "statics": {
        "max_rpm": 8000, "max_fuel": 120.0, "is_online": True,
        "player_name": "Yahia Farid FAR", "sector_count": 3,
        "aid_fuel_rate": 1.0, "aid_stability": 0.0, "aid_tyre_rate": 1.0,
        "aid_auto_clutch": True, "penalty_enabled": True,
        "aid_mechanical_damage": 0.800000011920929,
    },
}

MIN_SAMPLES = 100


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def recover(fail_path: Path) -> None:
    session_dir = fail_path.parent
    session_id  = session_dir.name
    stem        = fail_path.stem.replace(".failed", "")   # "lap_000"
    parquet_dst = session_dir / f"{stem}.parquet"
    meta_dst    = session_dir / f"{stem}.meta.json"

    if meta_dst.exists():
        print(f"  SKIP {stem}: meta.json already exists")
        return

    print(f"  Reading {fail_path.name} ... ", end="", flush=True)
    payload    = json.loads(fail_path.read_text(encoding="utf-8"))
    samples    = payload["samples"]
    lap_index  = payload["lap_index"]
    df         = pd.DataFrame(samples)
    n          = len(df)
    print(f"{n} samples")

    if n < MIN_SAMPLES:
        print(f"  SKIP {stem}: only {n} samples (< {MIN_SAMPLES})")
        return

    # Derive lap_time from the peak current_time in the lap buffer.
    # lap_time is in seconds; it resets to ~0 at the lap boundary, so max() is
    # the closest approximation to the final lap time.
    lap_time_ms: int
    if "lap_time" in df.columns:
        peak_s = df["lap_time"].max()
        lap_time_ms = int(peak_s * 1000)
    else:
        print(f"  WARN {stem}: no lap_time column — using 0ms")
        lap_time_ms = 0

    # Mark valid if the lap is longer than 90 s (outlaps / anomalous runs are shorter
    # or are lap_index 0). We can't recover the ACC validity flag from sample data.
    valid = lap_index > 0 and lap_time_ms > 90_000

    # Parse car/track from session directory name: YYYYMMDD_HHMMSS_car_track
    parts = session_id.split("_", 2)
    if len(parts) < 3:
        print(f"  ERROR {stem}: cannot parse session_id {session_id}")
        return
    remainder = parts[2]    # "ferrari_296_gt3_Silverstone" or "ferrari_296_gt3_Paul_Ricard"
    # Last token is track, everything before is car
    tokens    = remainder.rsplit("_", 1)
    car, track = tokens[0], tokens[1]

    # Write parquet atomically
    tmp_p = parquet_dst.with_name(f"{stem}.recover.parquet.tmp")
    df.to_parquet(tmp_p, index=False)
    os.replace(tmp_p, parquet_dst)
    checksum = _sha256(parquet_dst)

    meta = {
        **SESSION_STATICS,
        "session_id":     session_id,
        "car":            car,
        "track":          track,
        "lap_index":      lap_index,
        "lap_time_ms":    lap_time_ms,
        "valid":          valid,
        "sample_count":   n,
        "parquet_file":   f"{stem}.parquet",
        "parquet_sha256": checksum,
        "parquet_bytes":  parquet_dst.stat().st_size,
        "coords_file":    None,
    }
    tmp_m = meta_dst.with_suffix(".json.tmp")
    tmp_m.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(tmp_m, meta_dst)

    lap_s = lap_time_ms / 1000.0
    m, s  = divmod(lap_s, 60)
    print(f"  OK {stem}  {int(m)}:{s:06.3f}  {'VALID' if valid else 'invalid'}  -> {parquet_dst.relative_to(_ROOT)}")


def main() -> None:
    failed = sorted(_RAW.rglob("*.failed.json"))
    if not failed:
        print("No .failed.json files found in data/raw/.")
        return

    print(f"Found {len(failed)} failed lap(s):")
    for f in failed:
        print(f"\n[{f.parent.name}]")
        try:
            recover(f)
        except Exception as exc:
            print(f"  ERROR: {exc}")


if __name__ == "__main__":
    sys.exit(main())
