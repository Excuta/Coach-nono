"""
Logging setup for the capture agent.
Console: human-readable format (same as original basicConfig).
File:    JSON lines, size-rotating with gzip on rollover, capped at ~50 MB.
"""
from __future__ import annotations

import ctypes
import gzip
import json
import logging
import logging.handlers
import os
import shutil
import sys
import time
from pathlib import Path

def _has_console() -> bool:
    """Return True if a real console window is attached to this process.

    In Python 3.12+ pythonw.exe exposes sys.stderr as a live TextIOWrapper
    rather than None.  Writing to it when no console exists causes Windows to
    allocate a new console window — visible as a brief flash on lap crossing.
    GetConsoleWindow() == 0 is the reliable way to detect windowless mode.
    """
    try:
        return ctypes.windll.kernel32.GetConsoleWindow() != 0
    except Exception:
        return sys.stderr is not None


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "ts":    self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "msg":   record.getMessage(),
        }, ensure_ascii=False)


def _make_gzip_rotator(backup_count: int, log_dir: Path):
    """
    Returns a rotator that gzips the rolled log and enforces backup_count.
    RotatingFileHandler uses numbered dest names (capture.log.1) which don't match
    our timestamp scheme, so we bypass the handler's cleanup and do our own.
    """
    def _rotator(source: str, dest: str) -> None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        stem = Path(source).stem  # "capture"
        gz   = log_dir / f"{stem}.{ts}.log.gz"
        with open(source, "rb") as f_in, gzip.open(gz, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        os.remove(source)
        # Handler's numbered dest won't be created; remove it if it somehow exists.
        if os.path.exists(dest) and dest != source:
            try:
                os.remove(dest)
            except OSError:
                pass
        # Enforce backup_count: keep only the newest N gz files.
        existing = sorted(log_dir.glob(f"{stem}.*.log.gz"), key=lambda p: p.stat().st_mtime)
        for old in existing[:-backup_count] if backup_count else []:
            try:
                old.unlink()
            except OSError:
                pass
    return _rotator


def _namer(name: str) -> str:
    # The rotator bypasses the handler's dest; return name unchanged as placeholder.
    return name


def setup_logging(
    log_dir: Path | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """
    Wire up root logger. Call once at process start.
    Returns the 'capture' logger.

    log_dir: if given, also write JSON-lines to <log_dir>/capture.log
             with size-based rotation + gzip. Directory is created if absent.
    """
    console_fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # Console handler — only when a real console exists.
    # pythonw.exe in Python 3.12+ exposes sys.stderr as a live stream; writing
    # to it without an attached console causes Windows to flash a terminal window.
    if _has_console():
        ch = logging.StreamHandler()
        ch.setFormatter(console_fmt)
        root.addHandler(ch)

    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)

        # Main log: JSON lines, 5 MB per file, 10 rolled files (~5 MB on disk after gzip)
        fh = logging.handlers.RotatingFileHandler(
            log_dir / "capture.log",
            maxBytes=5_000_000,
            backupCount=10,
            encoding="utf-8",
        )
        fh.setFormatter(_JsonFormatter())
        fh.rotator = _make_gzip_rotator(backup_count=10, log_dir=log_dir)
        fh.namer = _namer
        root.addHandler(fh)

        # Audit log: one line per lap written, very small
        audit_path = log_dir / "capture-audit.log"
        ah = logging.handlers.RotatingFileHandler(
            audit_path,
            maxBytes=1_000_000,
            backupCount=10,
            encoding="utf-8",
        )
        ah.setFormatter(logging.Formatter("%(message)s"))
        audit_log = logging.getLogger("capture.audit")
        audit_log.addHandler(ah)
        audit_log.propagate = False

    return logging.getLogger("capture")
