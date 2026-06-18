"""
Notification module for the capture agent.
Primary:  BurntToast PowerShell module (Windows toast, works in borderless fullscreen).
Backstop: HTTP webhook (Discord/Telegram) — set CAPTURE_ALERT_WEBHOOK env var.
Both channels degrade gracefully if unavailable; a missing BurntToast or failed
webhook never crashes the capture loop.
Per-event cooldowns prevent spam during sustained error conditions.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time

log = logging.getLogger("capture.notify")

_WEBHOOK_URL: str | None = os.getenv("CAPTURE_ALERT_WEBHOOK")

# Minimum seconds between successive notifications of the same type.
_COOLDOWN: dict[str, float] = {
    "lap_discarded":   60.0,
    "lap_write_failed": 0.0,   # data loss — always fire
    "disk_low":       300.0,
    "disk_critical":   60.0,
    "session_lost":   120.0,
    "orphans":          0.0,
}


class Notifier:
    def __init__(self, webhook_url: str | None = _WEBHOOK_URL) -> None:
        self._webhook_url = webhook_url
        self._last: dict[str, float] = {}

    # ------------------------------------------------------------------ #
    # Public events
    # ------------------------------------------------------------------ #

    def lap_discarded(self, lap_index: int, sample_count: int, min_samples: int) -> None:
        msg = f"Lap {lap_index} discarded ({sample_count} samples < {min_samples} min)"
        self._notify("lap_discarded", "Coach Nono", msg, webhook=False)

    def lap_write_failed(self, lap_index: int, error: Exception) -> None:
        msg = f"Lap {lap_index} write FAILED: {error}"
        self._notify("lap_write_failed", "Coach Nono — DATA LOSS", msg, webhook=True)

    def disk_low(self, free_gb: float, threshold_gb: float) -> None:
        msg = f"Disk low: {free_gb:.1f} GB free (alert at {threshold_gb:.0f} GB) — ingest sweep triggered"
        self._notify("disk_low", "Coach Nono", msg, webhook=True)

    def disk_critical(self, free_gb: float) -> None:
        msg = f"Disk critical: {free_gb:.1f} GB free — capture PAUSED"
        self._notify("disk_critical", "Coach Nono — DISK CRITICAL", msg, webhook=True)

    def session_lost(self) -> None:
        self._notify("session_lost", "Coach Nono", "Lost connection to ACC", webhook=False)

    def orphans_repaired(self, count: int) -> None:
        msg = f"Startup recovery: repaired {count} orphaned lap file(s)"
        self._notify("orphans", "Coach Nono", msg, webhook=False)

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _cooldown_ok(self, event_type: str) -> bool:
        cooldown = _COOLDOWN.get(event_type, 60.0)
        if cooldown == 0.0:
            return True
        return (time.monotonic() - self._last.get(event_type, 0.0)) >= cooldown

    def _notify(self, event_type: str, title: str, message: str, *, webhook: bool) -> None:
        if not self._cooldown_ok(event_type):
            return
        self._last[event_type] = time.monotonic()
        log.info("[notify] %s: %s", title, message)
        self._toast(title, message)
        if webhook:
            self._webhook(title, message)

    def _toast(self, title: str, message: str) -> None:
        safe_title = title.replace("'", "")
        safe_msg   = message.replace("'", "")
        cmd = (
            f"New-BurntToastNotification -Text '{safe_title}', '{safe_msg}'"
        )
        try:
            subprocess.run(
                ["powershell", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", cmd],
                timeout=5,
                capture_output=True,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
            )
        except Exception as exc:
            log.debug("Toast failed (BurntToast not installed?): %s", exc)

    def _webhook(self, title: str, message: str) -> None:
        if not self._webhook_url:
            return
        try:
            import requests  # optional dependency
            requests.post(
                self._webhook_url,
                json={"content": f"**{title}** {message}"},
                timeout=5,
            )
        except Exception as exc:
            log.debug("Webhook failed: %s", exc)
