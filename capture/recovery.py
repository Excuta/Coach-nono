"""
Startup recovery scan for the capture agent.
Runs once at process start before the main loop.

Repairs:
  1. Stale *.tmp files left by a previous kill mid-write — delete them.
  2. Orphaned parquets (parquet exists, meta.json missing) — log them so the
     user knows data may need manual recovery; cannot auto-recover without
     the original session context.

Returns the count of stale tmp files removed.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("capture.recovery")


def run_recovery(raw_dir: Path, coords_dir: Path) -> int:
    """
    Scan raw_dir and coords_dir for stale .tmp files and orphaned parquets.
    Returns number of .tmp files removed.
    """
    removed = 0

    for search_dir in (raw_dir, coords_dir):
        if not search_dir.exists():
            continue
        for tmp in search_dir.rglob("*.tmp"):
            log.warning("Startup recovery: removing stale tmp: %s", tmp.relative_to(search_dir.parent))
            try:
                tmp.unlink()
                removed += 1
            except OSError as exc:
                log.error("Could not remove %s: %s", tmp, exc)

    if raw_dir.exists():
        for parquet in raw_dir.rglob("lap_*.parquet"):
            stem = parquet.stem          # "lap_001"
            meta = parquet.with_name(f"{stem}.meta.json")
            if not meta.exists():
                log.warning(
                    "Startup recovery: orphaned parquet (no meta) — manual check needed: %s",
                    parquet.relative_to(raw_dir.parent),
                )

    if removed:
        log.info("Startup recovery: removed %d stale tmp file(s)", removed)

    return removed
