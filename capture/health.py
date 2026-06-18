"""
Heartbeat health reporter for the capture agent.
Writes data/logs/capture/status.json atomically every ~2 s.
The file lives under the bind-mounted ./data directory so Docker services
(dashboard, sweeper) can read it without any extra mounts or ports.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Single-instance lockfile — prevents service + manual launch from colliding
# ---------------------------------------------------------------------------

_LOCK_PATH: Path | None = None  # set by claim_lockfile(), cleared by release_lockfile()


def claim_lockfile(data_dir: Path) -> Path:
    """
    Write a PID lockfile.  Raises RuntimeError if another instance is running.
    Call once at process start, before any other setup.
    """
    lock = data_dir / "logs" / "capture" / "capture.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)

    if lock.exists():
        raw = lock.read_text(encoding="utf-8").strip()
        try:
            existing_pid = int(raw)
            os.kill(existing_pid, 0)  # raises OSError if dead, passes if alive
            raise RuntimeError(
                f"Another capture agent is already running (PID {existing_pid}). "
                f"Stop it first, or delete {lock} if stale."
            )
        except (ValueError, OSError):
            logging.getLogger("capture.health").warning(
                "Stale lockfile (PID %s no longer running) — overwriting", raw
            )

    lock.write_text(str(os.getpid()), encoding="utf-8")
    global _LOCK_PATH
    _LOCK_PATH = lock
    return lock


def release_lockfile(lock: Path | None = None) -> None:
    """Delete the lockfile written by claim_lockfile()."""
    target = lock or _LOCK_PATH
    if target is not None:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass

log = logging.getLogger("capture.health")

_WRITE_INTERVAL = 2.0  # seconds between status.json writes


class HealthReporter:
    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "logs" / "capture" / "status.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._last_write = 0.0
        self._state: dict = {
            "pid":                  os.getpid(),
            "started_at":           time.strftime("%Y-%m-%dT%H:%M:%S"),
            "last_heartbeat":       time.strftime("%Y-%m-%dT%H:%M:%S"),
            "state":                "idle",
            "session_id":           None,
            "current_lap":          0,
            "laps_written_session": 0,
            "laps_written_total":   0,
            "free_disk_gb":         None,
            "unprocessed_raw_mb":   0.0,
            "unprocessed_lap_count": 0,
            "last_error":           None,
            "last_lap_written_at":  None,
            "sweep_trigger_active": False,
        }

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def update(self, force: bool = False, **kwargs) -> None:
        """Merge kwargs into state and write if interval elapsed (or force=True)."""
        self._state.update(kwargs)
        now = time.monotonic()
        if force or (now - self._last_write) >= _WRITE_INTERVAL:
            self._write()
            self._last_write = now

    def lap_written(self, session_id: str, lap_index: int, total: int) -> None:
        self.update(
            session_id=session_id,
            current_lap=lap_index,
            laps_written_session=lap_index,
            laps_written_total=total,
            last_lap_written_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        )

    def close(self) -> None:
        self.update(force=True, state="stopped")

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _write(self) -> None:
        self._state["last_heartbeat"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        tmp = self._path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(self._state, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
        except Exception as exc:
            log.debug("status.json write failed: %s", exc)
