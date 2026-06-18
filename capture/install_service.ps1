# install_service.ps1
# Installs the Coach Nono capture agent as a Windows service via NSSM.
#
# Prerequisites:
#   - Run as Administrator (right-click -> "Run as administrator" or elevated PS)
#   - nssm.exe on PATH  (download from https://nssm.cc/download)
#   - The capture venv must exist -- run run_capture.ps1 once first to create it
#
# The service is installed with Start=Manual for Phase 2 validation.
# After confirming capture works via the service, promote to auto-start:
#   nssm set CoachNono-Capture Start SERVICE_AUTO_START
# (or run this script again after Phase 3 cutover)

$ErrorActionPreference = "Stop"
$ServiceName = "CoachNono-Capture"
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot    = Split-Path -Parent $ScriptDir
$PythonW     = Join-Path $ScriptDir "venv\Scripts\pythonw.exe"
$AgentScript = Join-Path $ScriptDir "capture_agent.py"
$LogDir      = Join-Path $RepoRoot  "data\logs\capture"

# ---------------------------------------------------------------------------
# 1. Require elevation
# ---------------------------------------------------------------------------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw "Must run as Administrator. Re-launch in an elevated PowerShell."
}

# ---------------------------------------------------------------------------
# 2. Locate nssm.exe
# ---------------------------------------------------------------------------
$nssmCmd = Get-Command nssm.exe -ErrorAction SilentlyContinue
if (-not $nssmCmd) {
    # Fallback: look in the capture directory alongside this script
    $local = Join-Path $ScriptDir "nssm.exe"
    if (Test-Path $local) { $nssmCmd = @{ Source = $local } }
}
if (-not $nssmCmd) {
    throw "nssm.exe not found on PATH or in $ScriptDir.`nDownload from https://nssm.cc/download and add to PATH."
}
$nssm = $nssmCmd.Source
Write-Host "Using NSSM: $nssm"

# ---------------------------------------------------------------------------
# 3. Validate venv
# ---------------------------------------------------------------------------
if (-not (Test-Path $PythonW)) {
    throw "pythonw.exe not found at:`n  $PythonW`nRun run_capture.ps1 once first to create the venv."
}
Write-Host "Python: $PythonW"

# ---------------------------------------------------------------------------
# 4. Ensure log directory exists
# ---------------------------------------------------------------------------
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# ---------------------------------------------------------------------------
# 5. Service account credentials
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "ACC shared memory lives in the interactive user session."
Write-Host "The service MUST run as your Windows user account, NOT LocalSystem."
Write-Host "Current user: $env:COMPUTERNAME\$env:USERNAME"
Write-Host ""
$cred = Get-Credential `
    -UserName "$env:COMPUTERNAME\$env:USERNAME" `
    -Message  "Enter your Windows password for the CoachNono-Capture service account"
$plainPwd = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($cred.Password))

# ---------------------------------------------------------------------------
# 6. Remove existing service if present
# ---------------------------------------------------------------------------
$existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Removing existing $ServiceName..."
    if ($existing.Status -eq "Running") {
        Stop-Service -Name $ServiceName -Force
        Start-Sleep -Seconds 2
    }
    & $nssm remove $ServiceName confirm
}

# ---------------------------------------------------------------------------
# 7. Install and configure
# ---------------------------------------------------------------------------
Write-Host "Installing $ServiceName..."

& $nssm install $ServiceName $PythonW
& $nssm set $ServiceName AppParameters             "`"$AgentScript`""
& $nssm set $ServiceName AppDirectory              $RepoRoot
& $nssm set $ServiceName DisplayName               "Coach Nono Capture Agent"
& $nssm set $ServiceName Description               "ACC telemetry capture -- reads Win32 shared memory, writes to data/raw/"

# Run as the interactive user (required for ACC shared memory access)
& $nssm set $ServiceName ObjectName                ".\$env:USERNAME" $plainPwd

# Manual start for Phase 2 validation; promote with SERVICE_AUTO_START in Phase 3
& $nssm set $ServiceName Start                     SERVICE_DEMAND_START

# Restart policy: back off 10 s before each restart to avoid a tight crash loop
& $nssm set $ServiceName AppThrottle               10000
& $nssm set $ServiceName AppExit                   Default Restart

# Stdout/stderr routed to log files (append mode = disposition 4)
& $nssm set $ServiceName AppStdout                 "$LogDir\service-stdout.log"
& $nssm set $ServiceName AppStderr                 "$LogDir\service-stderr.log"
& $nssm set $ServiceName AppStdoutCreationDisposition 4
& $nssm set $ServiceName AppStderrCreationDisposition 4

# Optional env overrides -- uncomment and set as needed:
# & $nssm set $ServiceName AppEnvironmentExtra "CAPTURE_COORDS=true"
# & $nssm set $ServiceName AppEnvironmentExtra "CAPTURE_ALERT_WEBHOOK=https://discord.com/api/webhooks/..."
# & $nssm set $ServiceName AppEnvironmentExtra "CAPTURE_MIN_FREE_GB=10"

Write-Host ""
Write-Host "Service installed (Start=Manual for validation)."
Write-Host ""
Write-Host "VALIDATION STEPS"
Write-Host "  1. Start the service:"
Write-Host "       Start-Service $ServiceName"
Write-Host "  2. Confirm it idles (ACC not running):"
Write-Host "       Get-Service $ServiceName"
Write-Host "       Get-Content '$LogDir\service-stdout.log' -Tail 20"
Write-Host "  3. Open ACC, go on track, confirm laps appear in data\raw\"
Write-Host "  4. Check status.json:"
Write-Host "       Get-Content '$LogDir\status.json'"
Write-Host ""
Write-Host "When satisfied, promote to auto-start (Phase 3):"
Write-Host "  nssm set $ServiceName Start SERVICE_AUTO_START"
Write-Host ""
Write-Host "To uninstall:  .\uninstall_service.ps1"
