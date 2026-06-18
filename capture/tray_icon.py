"""
System tray viewer for the Coach Nono capture agent.

Reads data/logs/capture/status.json every 5 s and colors the icon:
  green  -- agent idle or live, heartbeat fresh
  amber  -- heartbeat stale, or state=lost/paused_disk
  red    -- status.json missing or state=stopped

Right-click menu: state label, open dashboard, stop/start capture, exit.
"""
import json
import subprocess
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

_HERE           = Path(__file__).parent
_REPO_ROOT      = _HERE.parent
_STATUS         = _REPO_ROOT / "data" / "logs" / "capture" / "status.json"
_LAST_COACHING  = _REPO_ROOT / "data" / "logs" / "process" / "last_coaching.json"

POLL_SECONDS  = 5
STALE_SECONDS = 30
DASHBOARD_URL = "http://localhost:8502"

_coaching_mtime: float = 0.0


def _make_icon(color: str) -> Image.Image:
    img  = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([4, 4, 60, 60], fill=color, outline=(255, 255, 255, 180), width=2)
    return img


ICON_GREEN = _make_icon("#4CAF50")
ICON_AMBER = _make_icon("#FF9800")
ICON_RED   = _make_icon("#F44336")


def _read_status():
    """Returns (icon_image, tooltip_str, state_label_str)."""
    if not _STATUS.exists():
        return ICON_RED, "Coach Nono\nCapture agent not running", "Not running"

    try:
        s = json.loads(_STATUS.read_text(encoding="utf-8"))
    except Exception:
        return ICON_AMBER, "Coach Nono\nstatus.json unreadable", "Unreadable"

    state = s.get("state", "unknown")
    hb    = s.get("last_heartbeat", "")
    try:
        age = int((datetime.now() - datetime.fromisoformat(hb)).total_seconds())
    except Exception:
        age = 9999

    stale = age > STALE_SECONDS

    if state == "stopped":
        icon = ICON_RED
    elif stale or state in ("lost", "paused_disk", "error"):
        icon = ICON_AMBER
    else:
        icon = ICON_GREEN

    laps_s  = s.get("laps_written_session", 0)
    laps_t  = s.get("laps_written_total", 0)
    disk    = s.get("free_disk_gb")
    disk_s  = f"\n  Disk: {disk:.0f} GB free" if disk else ""
    stale_s = " [STALE]" if stale else ""

    tip = (
        f"Coach Nono -- {state.upper()}{stale_s}"
        f"\n  Heartbeat: {age}s ago"
        f"\n  Laps this session: {laps_s}  total: {laps_t}"
        + disk_s
    )
    label = f"{state.upper()}{stale_s}  ({age}s ago)"
    return icon, tip, label


def _run_ps(command: str) -> None:
    subprocess.Popen(
        ["powershell", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", command],
        creationflags=0x08000000,  # CREATE_NO_WINDOW
    )


def _build_menu(label: str, tray_icon) -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem(label, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open Dashboard",  lambda: webbrowser.open(DASHBOARD_URL)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Stop Capture",    lambda: _run_ps("Stop-ScheduledTask CoachNono-Capture")),
        pystray.MenuItem("Start Capture",   lambda: _run_ps("Start-ScheduledTask CoachNono-Capture")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit Tray",       lambda: tray_icon.stop()),
    )


def _check_lap_coaching() -> None:
    """Fire a BurntToast notification when a new coaching result lands."""
    global _coaching_mtime
    if not _LAST_COACHING.exists():
        return
    try:
        mtime = _LAST_COACHING.stat().st_mtime
        if mtime <= _coaching_mtime:
            return
        _coaching_mtime = mtime
        data = json.loads(_LAST_COACHING.read_text(encoding="utf-8"))
        if not data.get("valid"):
            return
        lap_str = data.get("lap_time_str", "?")
        delta   = data.get("delta_s")
        title   = f"Lap {lap_str}"
        if delta is not None:
            sign = "+" if delta >= 0 else ""
            title += f"  {sign}{delta:.3f}s"
        snippets = data.get("top_findings", [])
        body = "  ·  ".join(snippets) if snippets else "No technique findings"
        safe_title = title.replace("'", "").replace('"', "")
        safe_body  = body.replace("'", "").replace('"', "")
        _run_ps(f"New-BurntToastNotification -Text '{safe_title}', '{safe_body}'")
    except Exception:
        pass


def _poll(tray_icon: pystray.Icon) -> None:
    while True:
        time.sleep(POLL_SECONDS)
        img, tip, label = _read_status()
        tray_icon.icon  = img
        tray_icon.title = tip
        tray_icon.menu  = _build_menu(label, tray_icon)
        _check_lap_coaching()


def main() -> None:
    img, tip, label = _read_status()
    tray_icon = pystray.Icon("CoachNono", img, tip)
    tray_icon.menu = _build_menu(label, tray_icon)
    threading.Thread(target=_poll, args=(tray_icon,), daemon=True).start()
    tray_icon.run()


if __name__ == "__main__":
    main()
