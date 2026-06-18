"""
Ingest sweep service — two jobs:

1. Catch-up ingest: drains any *.meta.json files left in data/raw/ while the
   primary ingest worker was down. Safe to run alongside live ingest — the
   underlying _process() uses shutil.move (atomic on the same fs) and
   idempotent DB ops, so two callers racing on the same file is harmless.

2. Disk reporting: logs byte-counts for long-term stores (laps/, coords/,
   findings/) in the structured sweep log every run. Pruning is a future
   concern; this cycle only does reporting.

Adaptive sleep — runs more frequently when there is a large backlog or a
disk-pressure trigger from capture_agent.py.

Sentinel handshake with the capture agent:
  - Reads  data/logs/capture/TRIGGER_INGEST      -> run immediately
  - Writes data/logs/capture/TRIGGER_INGEST_DONE -> unblocks a paused capture
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import time
from pathlib import Path

from coach.config import cfg

# Shared ingest code path.  Safe to import: shutil.move + ON CONFLICT DO NOTHING
# prevent double-processing even when ingest and sweep race on the same file.
from coach.ingest import _process  # noqa: PLC0415

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_CAPTURE_LOG_DIR = cfg.data_dir / "logs" / "capture"
_SWEEP_LOG_DIR   = cfg.data_dir / "logs" / "sweep"
_STATUS_JSON     = _CAPTURE_LOG_DIR / "status.json"
_TRIGGER_FILE    = _CAPTURE_LOG_DIR / "TRIGGER_INGEST"
_DONE_FILE       = _CAPTURE_LOG_DIR / "TRIGGER_INGEST_DONE"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=getattr(logging, cfg.log_level, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sweep")


def _init_sweep_log() -> logging.Logger:
    """One JSON line per sweep run, rotating at 1 MB / 10 files."""
    _SWEEP_LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.handlers.RotatingFileHandler(
        _SWEEP_LOG_DIR / "sweep.log",
        maxBytes=1_000_000,
        backupCount=10,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter("%(message)s"))
    slog = logging.getLogger("sweep.audit")
    slog.addHandler(fh)
    slog.propagate = False
    return slog


sweep_audit = _init_sweep_log()

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

_MAX_INTERVAL_S  = 30 * 60   # default between sweeps
_LIVE_DEFER_S    =  5 * 60   # back off when ACC is actively running
_HIGH_LOAD_S     = 10 * 60   # backlog > 500 MB
_MED_LOAD_S      = 20 * 60   # backlog > 100 MB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_capture_status() -> dict:
    try:
        return json.loads(_STATUS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _dir_size_gb(path: Path) -> float:
    if not path.exists():
        return 0.0
    total = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return round(total / 1e9, 3)


def _scan_backlog(raw_dir: Path) -> tuple[int, float]:
    """Return (meta_count, parquet_mb) of unprocessed laps in raw/."""
    metas = list(raw_dir.rglob("*.meta.json"))
    mb = 0.0
    for meta_path in metas:
        try:
            meta     = json.loads(meta_path.read_text(encoding="utf-8"))
            pq_path  = meta_path.parent / meta.get("parquet_file", "")
            if pq_path.exists():
                mb += pq_path.stat().st_size / 1e6
        except Exception:
            pass
    return len(metas), mb


def _next_sleep_s(count: int, mb: float, capture_state: str) -> int:
    # Back off while ACC is active to avoid storage I/O competing with capture
    if capture_state == "live":
        return _LIVE_DEFER_S
    if mb > 500:
        return _HIGH_LOAD_S
    if mb > 100:
        return _MED_LOAD_S
    return _MAX_INTERVAL_S


def _run_catchup(raw_dir: Path) -> tuple[int, int]:
    """Process all meta files in raw/. Returns (ingested, skipped)."""
    ingested = skipped = 0
    for meta_path in sorted(raw_dir.rglob("*.meta.json")):
        try:
            _process(meta_path)
            ingested += 1
        except FileNotFoundError:
            # Primary ingest worker moved the file first — expected race, not an error
            skipped += 1
        except Exception as exc:
            log.error("Sweep: failed to ingest %s: %s", meta_path.name, exc)
            skipped += 1
    return ingested, skipped


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run() -> None:
    log.info("Sweep service started. data_dir=%s", cfg.data_dir)
    cfg.raw_dir.mkdir(parents=True, exist_ok=True)

    while True:
        t0 = time.monotonic()

        # ----- Check for disk-pressure trigger from capture agent -----
        triggered = _TRIGGER_FILE.exists()
        if triggered:
            log.info("Disk-pressure trigger detected — running immediately")
            try:
                _TRIGGER_FILE.unlink()
            except OSError:
                pass

        # ----- Read capture health -----
        status        = _read_capture_status()
        capture_state = status.get("state", "unknown")

        # ----- Scan backlog -----
        count_before, mb_before = _scan_backlog(cfg.raw_dir)
        log.info(
            "Sweep start: state=%s  backlog=%d laps / %.1f MB  triggered=%s",
            capture_state, count_before, mb_before, triggered,
        )

        # ----- Catch-up ingest -----
        if count_before > 0 or triggered:
            ingested, skipped = _run_catchup(cfg.raw_dir)
            log.info("Catch-up complete: ingested=%d  skipped=%d", ingested, skipped)
        else:
            log.debug("Backlog empty — nothing to process")
            ingested = skipped = 0

        # ----- Disk usage report -----
        disk = {
            "laps_gb":     _dir_size_gb(cfg.laps_dir),
            "coords_gb":   _dir_size_gb(cfg.coords_dir),
            "findings_gb": _dir_size_gb(cfg.findings_dir),
            "raw_gb":      _dir_size_gb(cfg.raw_dir),
            "logs_gb":     _dir_size_gb(cfg.data_dir / "logs"),
        }
        log.info(
            "Disk: laps=%.2f GB  coords=%.2f GB  findings=%.2f GB",
            disk["laps_gb"], disk["coords_gb"], disk["findings_gb"],
        )

        duration_s = round(time.monotonic() - t0, 2)

        # ----- Structured audit entry -----
        sweep_audit.info(json.dumps({
            "ts":                   time.strftime("%Y-%m-%dT%H:%M:%S"),
            "triggered_by":         "disk_alert" if triggered else "schedule",
            "capture_state":        capture_state,
            "backlog_before_count": count_before,
            "backlog_before_mb":    round(mb_before, 1),
            "laps_ingested":        ingested,
            "laps_skipped":         skipped,
            "disk":                 disk,
            "duration_s":           duration_s,
        }))

        # ----- Unblock a paused capture agent -----
        if triggered:
            try:
                _DONE_FILE.parent.mkdir(parents=True, exist_ok=True)
                _DONE_FILE.touch()
                log.info("Wrote TRIGGER_INGEST_DONE — capture agent can resume")
            except OSError as exc:
                log.warning("Could not write TRIGGER_INGEST_DONE: %s", exc)

        # ----- Adaptive sleep -----
        sleep_s = _next_sleep_s(count_before, mb_before, capture_state)
        log.info("Next sweep in %d min", sleep_s // 60)
        time.sleep(sleep_s)


if __name__ == "__main__":
    run()
