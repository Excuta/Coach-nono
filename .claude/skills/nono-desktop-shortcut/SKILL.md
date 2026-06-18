---
name: nono-desktop-shortcut
description: Creates a Coach Nono desktop shortcut and the scripts.ps1 launcher for the Coach Nono project. Use whenever the user says "create the desktop shortcut", "make the Coach Nono shortcut", "set up the shortcut", "create a terminal shortcut", or invokes /nono-desktop-shortcut. Always use this skill rather than writing ad-hoc PowerShell — it ensures scripts.ps1 is up-to-date and the shortcut points to the right place.
---

# nono-desktop-shortcut

Creates two artifacts:
1. **`scripts.ps1`** at the project root — prints all available project scripts with descriptions and colors, grouped by category
2. **`Coach Nono.lnk`** on the Windows Desktop — opens PowerShell at the project root and runs `scripts.ps1` before dropping to an interactive prompt

## Project root

Detect at invocation time — do not hardcode. Use PowerShell:

    $root = (git rev-parse --show-toplevel) -replace '/', '\'

Use `$root` wherever the project path is needed.

## Step 1 — Scan for scripts

Use PowerShell to find all `.ps1` files under the project root, excluding:
- `capture\venv\` (Python virtualenv, thousands of files)
- Any path containing `\node_modules\`

From each file, extract the **description**: the first line that starts with `#` and is not a shebang (`#!`). Strip the leading `#` and trim whitespace. If no comment is found use an empty string.

Always include these entries if the file exists (they are the primary user-facing scripts):
- `capture\run_capture.ps1`
- `capture\status.ps1`
- `capture\watch_laps.ps1`
- `capture\install_service.ps1`

Also include this Python entry point if it exists:
- `capture\capture_agent.py`

## Step 2 — Write scripts.ps1

> **PowerShell 5.1 encoding constraint:** use only plain ASCII characters. Unicode box-drawing characters (`─`, `—`, etc.) cause parse errors. Use `-` for rules and `--` for section headers.

Write the following file to `<project_root>\scripts.ps1`. Generate its content dynamically from the scan results, grouped into categories.

Grouping logic:
- **Capture** — files under `capture\`
- **Root** — files directly in the project root (including `scripts.ps1` itself)
- Any other subdirectory becomes its own group named after the folder

Format for each entry:
```
  <relative-path padded to 40 chars>  <description in DarkGray>
```

The script must:
- Print the header in Cyan: `Coach Nono — Project Scripts`
- Print the timestamp in DarkGray
- Print each group heading with a horizontal rule
- At the bottom, print a tip in DarkGray: `Run any script from the project root: .\<path>`
- Leave a blank line at the end

**Important:** write `scripts.ps1` so it is self-contained — hardcode the current list of scripts and descriptions rather than scanning at runtime. This way it works instantly when opened from the shortcut.

Example output shape (not literal content):
```
  Coach Nono — Project Scripts
  2026-06-18 15:30:00

  ── Capture ─────────────────────────────────────────────────
    capture\run_capture.ps1          Start the telemetry capture agent
    capture\status.ps1               Print data layer snapshot
    capture\watch_laps.ps1           Live lap feed (Ctrl+C to stop)
    capture\capture_agent.py         Telemetry capture agent (run via run_capture.ps1)

  ── Root ────────────────────────────────────────────────────
    scripts.ps1                      List all project scripts

  Run any script from the project root: .\<path>
```

## Step 3 — Create the desktop shortcut

Run this PowerShell (substitute the actual project root path):

```powershell
$root = (git rev-parse --show-toplevel) -replace '/', '\'
$ws   = New-Object -ComObject WScript.Shell
$lnk  = $ws.CreateShortcut("$($ws.SpecialFolders('Desktop'))\Coach Nono.lnk")
$lnk.TargetPath      = 'powershell.exe'
$lnk.Arguments       = "-NoExit -Command `"Set-Location '$root'; .\scripts.ps1`""
$lnk.WorkingDirectory = $root
$lnk.IconLocation    = 'powershell.exe,0'
$lnk.Description     = 'Coach Nono — open project terminal'
$lnk.Save()
```

## Step 4 — Output to user

1. Confirm: `shortcut created at <desktop path>`
2. Print the script listing immediately in the conversation (same format as `scripts.ps1`) so the user sees it without opening the shortcut
3. Note: "Double-click `Coach Nono` on the desktop to open a PowerShell terminal at the project root with this listing printed automatically."

## Regenerating

This skill is idempotent — running it again overwrites `scripts.ps1` and the shortcut. Run it whenever new scripts are added to the project.
