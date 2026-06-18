"""
ACC telemetry capture agent — runs on the Windows host (not in Docker).

Polls ACC shared memory at ~50 Hz, detects lap boundaries via completedLaps,
and writes one parquet + meta.json per completed lap to data/raw/<session_id>/.
Files are written atomically (*.tmp then os.replace) so the ingest worker
never sees a half-written lap.

If CAPTURE_COORDS=true, also writes per-lap car XYZ coordinate traces to
data/coords/<session_id>/ for future track-geometry fitting.

Usage:
    python capture_agent.py          # or via run_capture.ps1
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

# Allow importing shared schema + ACC adapter from the pipeline package tree.
sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "pipeline"))
from coach.sources.acc import ACCSource  # noqa: E402  (sys.path manipulation above)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("capture")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent
DATA_DIR = _ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
COORDS_DIR = DATA_DIR / "coords"

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
POLL_HZ = 50                  # target sample rate
POLL_INTERVAL = 1.0 / POLL_HZ
MIN_SAMPLES = 500             # ~10 s at 50 Hz; silently discard shorter laps
_ACC_LIVE = 2                 # ACC_STATUS.ACC_LIVE
CAPTURE_COORDS = os.getenv("CAPTURE_COORDS", "").lower() in ("1", "true", "yes")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_name(s: str) -> str:
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in s).strip("_")[:32]


def _write_lap(
    buf: list,
    session_id: str,
    lap_index: int,
    lap_time_ms: int,
    valid: bool,
    ctx: dict,
    coords: list | None = None,
) -> None:
    if len(buf) < MIN_SAMPLES:
        log.warning("Lap %d: %d samples (< %d), discarding", lap_index, len(buf), MIN_SAMPLES)
        return

    session_dir = RAW_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    stem = f"lap_{lap_index:03d}"
    parquet_dst = session_dir / f"{stem}.parquet"
    meta_dst = session_dir / f"{stem}.meta.json"

    # --- Atomic parquet write ---
    tmp_p = parquet_dst.with_suffix(".parquet.tmp")
    df = pd.DataFrame([asdict(s) for s in buf])
    df.to_parquet(tmp_p, index=False)
    os.replace(tmp_p, parquet_dst)

    # --- Optional coords parquet write ---
    coords_file: str | None = None
    if coords:
        c_dir = COORDS_DIR / session_id
        c_dir.mkdir(parents=True, exist_ok=True)
        c_parquet = c_dir / f"{stem}.parquet"
        tmp_c = c_parquet.with_suffix(".parquet.tmp")
        pd.DataFrame(coords).to_parquet(tmp_c, index=False)
        os.replace(tmp_c, c_parquet)
        coords_file = f"{stem}.parquet"

    # --- Atomic meta write (always after parquet so ingest never sees meta without parquet) ---
    meta = {
        **ctx,
        "session_id": session_id,
        "lap_index": lap_index,
        "lap_time_ms": lap_time_ms,
        "valid": valid,
        "sample_count": len(buf),
        "parquet_file": f"{stem}.parquet",
        "coords_file": coords_file,
    }
    tmp_m = meta_dst.with_suffix(".json.tmp")
    tmp_m.write_text(json.dumps(meta, indent=2))
    os.replace(tmp_m, meta_dst)

    lap_s = lap_time_ms / 1000.0
    m, s = divmod(lap_s, 60)
    log.info(
        "Lap %d  %d:%06.3f  %s  %d samples  →  %s%s",
        lap_index, int(m), s,
        "VALID" if valid else "INVALID",
        len(buf),
        parquet_dst.relative_to(_ROOT),
        f"  +coords({len(coords)})" if coords_file else "",
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> None:
    source = ACCSource()
    source.open()
    log.info("Capture agent started. Waiting for ACC to go live… (CAPTURE_COORDS=%s)", CAPTURE_COORDS)

    session_id: str | None = None
    ctx: dict = {}
    t0: float = 0.0          # monotonic time at session start
    lap_buf: list = []
    coords_buf: list = []
    prev_completed: int = 0
    prev_session_index: int = -1
    lap_valid: bool = True    # tracks whether current lap is still valid

    try:
        while True:
            loop_start = time.monotonic()

            try:
                raw = source.read_shared_memory()
            except Exception as exc:
                log.debug("Shared-memory read failed: %s", exc)
                if session_id is not None:
                    log.warning("Lost connection to ACC.")
                    session_id = None
                    lap_buf = []
                    coords_buf = []
                time.sleep(1.0)
                continue

            if raw is None:
                time.sleep(POLL_INTERVAL)
                continue

            g = raw.Graphics
            status = g.status.value

            if status != _ACC_LIVE:
                if session_id is not None:
                    log.info("Left track (status=%d). Session %s paused.", status, session_id)
                    session_id = None
                    lap_buf = []
                    coords_buf = []
                time.sleep(POLL_INTERVAL)
                continue

            # ---- First live sample: initialise session ----
            if session_id is None:
                sc = source.context(raw)
                car = sc.car or "unknown_car"
                track = sc.track or "unknown_track"
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                session_id = f"{ts}_{_safe_name(car)}_{_safe_name(track)}"
                ctx = {
                    "game": sc.game,
                    "car": car,
                    "track": track,
                    "session_type": sc.session_type,
                    "conditions": sc.conditions,
                    "statics": sc.statics,
                }
                t0 = time.monotonic()
                prev_completed = g.completed_lap
                prev_session_index = g.session_index
                lap_valid = True
                log.info("Session started: %s  (car=%s  track=%s)", session_id, car, track)

            else:
                # ---- Detect new session via session_index (most reliable signal) ----
                # session_index increments for every new ACC session regardless of
                # whether car/track/type changed or status briefly dropped.
                if g.session_index != prev_session_index:
                    log.info(
                        "New session detected (session_index %d→%d) — resetting",
                        prev_session_index, g.session_index,
                    )
                    session_id = None
                    lap_buf = []
                    coords_buf = []
                    prev_completed = 0
                    prev_session_index = -1
                    continue

            completed = g.completed_lap

            # ---- Accumulate sample ----
            t = time.monotonic() - t0
            lap_buf.append(source.to_sample(raw, t))
            if CAPTURE_COORDS:
                coords_buf.append(source.coords_row(raw, t))

            # Track invalidity across the lap — is_valid_lap resets to True on
            # the crossing sample, so we must latch any False seen during the lap.
            if not g.is_valid_lap:
                lap_valid = False

            # ---- Lap boundary ----
            if completed > prev_completed:
                lap_time_ms = g.last_time
                _write_lap(
                    lap_buf, session_id, prev_completed, lap_time_ms, lap_valid, ctx,
                    coords=coords_buf if CAPTURE_COORDS else None,
                )
                lap_buf = []
                coords_buf = []
                prev_completed = completed
                lap_valid = True  # reset for incoming lap

            # ---- Throttle to poll rate ----
            sleep_for = POLL_INTERVAL - (time.monotonic() - loop_start)
            if sleep_for > 0:
                time.sleep(sleep_for)

    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C).")
    finally:
        source.close()
        log.info("Capture agent shut down.")


if __name__ == "__main__":
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if CAPTURE_COORDS:
        COORDS_DIR.mkdir(parents=True, exist_ok=True)
    run()
