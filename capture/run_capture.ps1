# run_capture.ps1 — launch the Coach Nono capture agent on the Windows host.
# Run this before going on track. Stop with Ctrl+C when the session is over.
#
# First run: creates a local venv and installs dependencies automatically.

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

Push-Location $ScriptDir
try {
    if (-not (Test-Path "venv")) {
        Write-Host "Creating capture venv (first run)..."
        python -m venv venv
        .\venv\Scripts\Activate.ps1
        pip install --upgrade pip -q
        pip install -r requirements.txt
    } else {
        .\venv\Scripts\Activate.ps1
    }

    Write-Host ""
    Write-Host "Coach Nono capture agent — press Ctrl+C to stop"
    Write-Host ""
    python capture_agent.py
} finally {
    Pop-Location
}
