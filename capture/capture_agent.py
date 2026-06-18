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

import hashlib
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd

# Allow importing shared schema + ACC adapter from the pipeline package tree.
sys.path.insert(0, str(Path(__file__).parent.parent / "services" / "pipeline"))
from coach.sources.acc import ACCSource  # noqa: E402

# Capture-local modules (same directory; importable because script dir is on sys.path)
from health import HealthReporter, claim_lockfile, release_lockfile
from logging_setup import setup_logging
from notify import Notifier
from recovery import run_recovery

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_ROOT      = Path(__file__).parent.parent
DATA_DIR   = _ROOT / "data"
RAW_DIR    = DATA_DIR / "raw"
COORDS_DIR = DATA_DIR / "coords"
LOG_DIR    = DATA_DIR / "logs" / "capture"

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------
POLL_HZ      = 50
POLL_INTERVAL = 1.0 / POLL_HZ
MIN_SAMPLES  = 500              # ~10 s at 50 Hz; discard shorter laps
_ACC_LIVE    = 2                # ACC_STATUS.ACC_LIVE
CAPTURE_COORDS = os.getenv("CAPTURE_COORDS", "").lower() in ("1", "true", "yes")

# Statics debounce — require this many consecutive mismatches before treating
# as a real server/car switch (filters out ~1-2 s statics lag on session change)
_STATICS_MISMATCH_THRESHOLD = 150   # 3 s at 50 Hz

# Disk thresholds
_ALERT_FREE_GB = float(os.getenv("CAPTURE_MIN_FREE_GB",      "5"))
_PAUSE_FREE_GB = float(os.getenv("CAPTURE_PAUSE_FREE_GB",    "2"))
_PAUSE_TIMEOUT = int(os.getenv("CAPTURE_PAUSE_TIMEOUT_S", "300"))

# Sentinel files for sweeper handshake
_TRIGGER_FILE  = LOG_DIR / "TRIGGER_INGEST"
_DONE_FILE     = LOG_DIR / "TRIGGER_INGEST_DONE"

# Logging is set up once at module level so it is available before run()
log = setup_logging(log_dir=LOG_DIR)
audit_log = logging.getLogger("capture.audit")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_name(s: str) -> str:
    return "".join(c if (c.isalnum() or c == "_") else "_" for c in s).strip("_")[:32]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _dump_failed_lap(buf: list, session_id: str, lap_index: int, error: Exception) -> None:
    """Last-resort dump when parquet write fails — saves raw samples as JSON."""
    session_dir = RAW_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    fail_path = session_dir / f"lap_{lap_index:03d}.failed.json"
    try:
        payload = {
            "error":     str(error),
            "lap_index": lap_index,
            "samples":   [asdict(s) for s in buf],
        }
        fail_path.write_text(json.dumps(payload), encoding="utf-8")
        log.error("Raw samples dumped to %s for manual recovery", fail_path.relative_to(_ROOT))
    except Exception as dump_exc:
        log.error("Could not dump failed lap either: %s", dump_exc)


def _check_disk(notifier: Notifier, health: HealthReporter) -> str:
    """
    Returns 'ok', 'alert', or 'pause'.
    Side-effects: updates health free_disk_gb, fires notifier, writes TRIGGER_INGEST.
    """
    free_bytes = shutil.disk_usage(DATA_DIR).free
    free_gb    = free_bytes / 1e9
    health.update(free_disk_gb=round(free_gb, 2))

    if free_gb < _PAUSE_FREE_GB:
        return "pause"
    if free_gb < _ALERT_FREE_GB:
        notifier.disk_low(free_gb, _ALERT_FREE_GB)
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _TRIGGER_FILE.touch()
        health.update(sweep_trigger_active=True, force=True)
        return "alert"
    return "ok"


def _pause_for_disk(notifier: Notifier, health: HealthReporter) -> None:
    """Block until disk recovers, sweeper signals done, or timeout expires."""
    free_gb = shutil.disk_usage(DATA_DIR).free / 1e9
    notifier.disk_critical(free_gb)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _TRIGGER_FILE.touch()
    health.update(state="paused_disk", sweep_trigger_active=True, force=True)
    log.warning("Disk critically low (%.1f GB) — pausing capture for up to %ds", free_gb, _PAUSE_TIMEOUT)

    deadline = time.monotonic() + _PAUSE_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(10)
        if _DONE_FILE.exists():
            try:
                _DONE_FILE.unlink()
            except OSError:
                pass
        free_gb = shutil.disk_usage(DATA_DIR).free / 1e9
        if free_gb >= _PAUSE_FREE_GB + 1.0:
            log.info("Disk recovered (%.1f GB free) — resuming", free_gb)
            health.update(state="live", sweep_trigger_active=False)
            return
    log.error("Disk pause timed out after %ds — resuming anyway", _PAUSE_TIMEOUT)
    health.update(state="live", sweep_trigger_active=False)


def _write_lap(
    buf: list,
    session_id: str,
    lap_index: int,
    lap_time_ms: int,
    valid: bool,
    ctx: dict,
    notifier: Notifier,
    coords: list | None = None,
) -> None:
    if len(buf) < MIN_SAMPLES:
        log.warning("Lap %d: %d samples (< %d), discarding", lap_index, len(buf), MIN_SAMPLES)
        notifier.lap_discarded(lap_index, len(buf), MIN_SAMPLES)
        return

    session_dir = RAW_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    stem        = f"lap_{lap_index:03d}"
    parquet_dst = session_dir / f"{stem}.parquet"
    meta_dst    = session_dir / f"{stem}.meta.json"

    # --- Atomic parquet write ---
    tmp_p = parquet_dst.with_suffix(".parquet.tmp")
    df = pd.DataFrame([asdict(s) for s in buf])
    df.to_parquet(tmp_p, index=False)
    os.replace(tmp_p, parquet_dst)

    # SHA-256 checksum for integrity verification by ingest / sweeper
    checksum = _sha256(parquet_dst)

    # --- Optional coords parquet write ---
    coords_file: str | None = None
    if coords:
        c_dir     = COORDS_DIR / session_id
        c_dir.mkdir(parents=True, exist_ok=True)
        c_parquet = c_dir / f"{stem}.parquet"
        tmp_c     = c_parquet.with_suffix(".parquet.tmp")
        pd.DataFrame(coords).to_parquet(tmp_c, index=False)
        os.replace(tmp_c, c_parquet)
        coords_file = f"{stem}.parquet"

    # --- Atomic meta write (always after parquet so ingest never sees meta without parquet) ---
    meta = {
        **ctx,
        "session_id":     session_id,
        "lap_index":      lap_index,
        "lap_time_ms":    lap_time_ms,
        "valid":          valid,
        "sample_count":   len(buf),
        "parquet_file":   f"{stem}.parquet",
        "parquet_sha256": checksum,
        "parquet_bytes":  parquet_dst.stat().st_size,
        "coords_file":    coords_file,
    }
    tmp_m = meta_dst.with_suffix(".json.tmp")
    tmp_m.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(tmp_m, meta_dst)

    lap_s = lap_time_ms / 1000.0
    m, s  = divmod(lap_s, 60)
    msg   = (
        f"Lap {lap_index}  {int(m)}:{s:06.3f}  "
        f"{'VALID' if valid else 'INVALID'}  {len(buf)} samples  "
        f"->  {parquet_dst.relative_to(_ROOT)}"
        + (f"  +coords({len(coords)})" if coords_file else "")
    )
    log.info(msg)
    audit_log.info(
        json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "session_id": session_id,
            "lap_index": lap_index,
            "lap_time_ms": lap_time_ms,
            "valid": valid,
            "sample_count": len(buf),
            "parquet_sha256": checksum,
        })
    )


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> None:
    lock = claim_lockfile(DATA_DIR)

    notifier = Notifier()
    health   = HealthReporter(DATA_DIR)

    source = ACCSource()
    source.open()
    log.info("Capture agent started. Waiting for ACC to go live... (CAPTURE_COORDS=%s)", CAPTURE_COORDS)
    health.update(state="idle", force=True)

    repaired = run_recovery(RAW_DIR, COORDS_DIR)
    if repaired:
        notifier.orphans_repaired(repaired)

    session_id: str | None = None
    ctx:        dict       = {}
    t0:         float      = 0.0
    lap_buf:    list       = []
    coords_buf: list       = []
    prev_completed:    int = 0
    prev_session_index:int = -1
    lap_valid:         bool = True
    statics_mismatch_count: int = 0
    laps_written_total: int = 0

    try:
        while True:
            loop_start = time.monotonic()

            try:
                raw = source.read_shared_memory()
            except Exception as exc:
                log.debug("Shared-memory read failed: %s", exc)
                if session_id is not None:
                    log.warning("Lost connection to ACC.")
                    notifier.session_lost()
                    health.update(state="lost", session_id=None, force=True)
                    session_id = None
                    lap_buf    = []
                    coords_buf = []
                time.sleep(1.0)
                continue

            if raw is None:
                time.sleep(POLL_INTERVAL)
                continue

            g      = raw.Graphics
            status = g.status.value

            if status != _ACC_LIVE:
                if session_id is not None:
                    log.info("Left track (status=%d). Session %s paused.", status, session_id)
                    health.update(state="idle", session_id=None, force=True)
                    session_id = None
                    lap_buf    = []
                    coords_buf = []
                else:
                    health.update(state="idle")
                time.sleep(POLL_INTERVAL)
                continue

            # ---- First live sample: initialise session ----
            if session_id is None:
                sc    = source.context(raw)
                car   = sc.car   or "unknown_car"
                track = sc.track or "unknown_track"
                ts    = datetime.now().strftime("%Y%m%d_%H%M%S")
                session_id = f"{ts}_{_safe_name(car)}_{_safe_name(track)}"
                ctx = {
                    "game":         sc.game,
                    "car":          car,
                    "track":        track,
                    "session_type": sc.session_type,
                    "conditions":   sc.conditions,
                    "statics":      sc.statics,
                }
                t0                 = time.monotonic()
                prev_completed     = g.completed_lap
                prev_session_index = g.session_index
                lap_valid          = True
                statics_mismatch_count = 0
                log.info("Session started: %s  (car=%s  track=%s)", session_id, car, track)
                health.update(state="live", session_id=session_id, current_lap=0,
                              laps_written_session=0, force=True)

            else:
                # ---- Signal 1: session_index change ----
                if g.session_index != prev_session_index:
                    log.info(
                        "New session detected (session_index %d->%d) -- resetting",
                        prev_session_index, g.session_index,
                    )
                    session_id         = None
                    lap_buf            = []
                    coords_buf         = []
                    prev_completed     = 0
                    prev_session_index = -1
                    statics_mismatch_count = 0
                    health.update(state="idle", session_id=None, force=True)
                    continue

                # ---- Signal 2: completed_lap regression ----
                if g.completed_lap < prev_completed:
                    log.info(
                        "Lap counter regressed (%d->%d) -- treating as new session",
                        prev_completed, g.completed_lap,
                    )
                    session_id         = None
                    lap_buf            = []
                    coords_buf         = []
                    prev_completed     = 0
                    prev_session_index = -1
                    statics_mismatch_count = 0
                    health.update(state="idle", session_id=None, force=True)
                    continue

                # ---- Signal 3: statics debounce (practice-server rejoins) ----
                cur_car   = raw.Static.car_model.rstrip("\x00").strip()
                cur_track = raw.Static.track.rstrip("\x00").strip()
                if cur_car != ctx["car"] or cur_track != ctx["track"]:
                    statics_mismatch_count += 1
                    if statics_mismatch_count >= _STATICS_MISMATCH_THRESHOLD:
                        log.info(
                            "Car/track changed (%s/%s -> %s/%s) -- treating as new session",
                            ctx["car"], ctx["track"], cur_car, cur_track,
                        )
                        session_id         = None
                        lap_buf            = []
                        coords_buf         = []
                        prev_completed     = 0
                        prev_session_index = -1
                        statics_mismatch_count = 0
                        health.update(state="idle", session_id=None, force=True)
                        continue
                else:
                    statics_mismatch_count = 0

            completed = g.completed_lap

            # ---- Accumulate sample ----
            t = time.monotonic() - t0
            lap_buf.append(source.to_sample(raw, t))
            if CAPTURE_COORDS:
                coords_buf.append(source.coords_row(raw, t))

            if not g.is_valid_lap:
                lap_valid = False

            # ---- Lap boundary ----
            if completed > prev_completed:

                # Disk check before writing — may pause here if critically low
                disk_status = _check_disk(notifier, health)
                if disk_status == "pause":
                    _pause_for_disk(notifier, health)

                try:
                    _write_lap(
                        lap_buf, session_id, prev_completed, g.last_time, lap_valid, ctx,
                        notifier,
                        coords=coords_buf if CAPTURE_COORDS else None,
                    )
                    laps_written_total += 1
                    health.lap_written(session_id, prev_completed, laps_written_total)
                except Exception as exc:
                    log.error("Lap write failed: %s", exc)
                    notifier.lap_write_failed(prev_completed, exc)
                    _dump_failed_lap(lap_buf, session_id, prev_completed, exc)

                lap_buf    = []
                coords_buf = []
                prev_completed = completed
                lap_valid      = True

            # ---- Heartbeat (throttled inside HealthReporter) ----
            health.update(current_lap=completed)

            # ---- Throttle to poll rate ----
            sleep_for = POLL_INTERVAL - (time.monotonic() - loop_start)
            if sleep_for > 0:
                time.sleep(sleep_for)

    except KeyboardInterrupt:
        log.info("Stopped by user (Ctrl+C).")
    finally:
        health.close()
        source.close()
        release_lockfile(lock)
        log.info("Capture agent shut down.")


if __name__ == "__main__":
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if CAPTURE_COORDS:
        COORDS_DIR.mkdir(parents=True, exist_ok=True)
    run()
