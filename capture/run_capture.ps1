# run_capture.ps1 - launch the Coach Nono capture agent on the Windows host.
# Run this before going on track. Stop with Ctrl+C when the session is over.
#
# First run: creates a local venv and installs dependencies automatically.

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Guard: bail if the NSSM service is already capturing.
# The Python lockfile also catches this, but failing here gives a cleaner message.
$svc = Get-Service -Name "CoachNono-Capture" -ErrorAction SilentlyContinue
if ($svc -and $svc.Status -eq "Running") {
    Write-Host "CoachNono-Capture service is already running -- not starting a second instance."
    Write-Host "  Stop service:   Stop-Service CoachNono-Capture"
    Write-Host "  Check status:   .\status.ps1"
    exit 1
}

# Find a real Python executable (skip the Microsoft Store stub)
$python = (Get-Command python*.exe -ErrorAction SilentlyContinue |
    Where-Object { $_.Source -notlike "*WindowsApps*" } |
    Select-Object -First 1).Source

if (-not $python) {
    throw "Python not found. Install Python 3.x from https://www.python.org and ensure it is on PATH."
}
Write-Host "Using Python: $python"

Push-Location $ScriptDir
try {
    if (-not (Test-Path "venv")) {
        Write-Host "Creating capture venv (first run)..."
        & $python -m venv venv
        .\venv\Scripts\Activate.ps1
        pip install --upgrade pip -q
        pip install -r requirements.txt
    } else {
        .\venv\Scripts\Activate.ps1
    }

    Write-Host ""
    Write-Host "Coach Nono capture agent - press Ctrl+C to stop"
    Write-Host ""
    python capture_agent.py
} finally {
    Pop-Location
}
