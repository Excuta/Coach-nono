# watchdog.ps1
# Single-shot: reads status.json, restarts CoachNono-Capture if heartbeat is stale.
# Registered as a repeating Scheduled Task (every 5 min) by install_service.ps1.
# If this script itself crashes, the next scheduled firing picks up automatically.

param(
    [int]$StaleThresholdSeconds = 45
)

$CaptureTask = "CoachNono-Capture"
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot    = Split-Path -Parent $ScriptDir
$StatusJson  = Join-Path $RepoRoot "data\logs\capture\status.json"
$WatchdogLog = Join-Path $RepoRoot "data\logs\capture\watchdog.log"

function Write-Log($msg) {
    $line = "$(Get-Date -Format 'yyyy-MM-ddTHH:mm:ss') $msg"
    Add-Content -Path $WatchdogLog -Value $line -Encoding UTF8
}

function Send-Toast($title, $body) {
    $cmd = "New-BurntToastNotification -Text '$title','$body'"
    try { powershell -WindowStyle Hidden -NonInteractive -Command $cmd } catch {}
}

# No status.json yet -- capture has never run, nothing to watch
if (-not (Test-Path $StatusJson)) { exit 0 }

try {
    $s   = Get-Content $StatusJson -Raw -Encoding UTF8 | ConvertFrom-Json
    $age = [int](New-TimeSpan -Start ([datetime]$s.last_heartbeat) -End (Get-Date)).TotalSeconds
} catch {
    Write-Log "[WARN] Could not parse status.json: $_"
    exit 0
}

if ($age -le $StaleThresholdSeconds) { exit 0 }  # heartbeat is fresh

# Heartbeat stale -- check if the task is even registered
$task = Get-ScheduledTask -TaskName $CaptureTask -ErrorAction SilentlyContinue
if (-not $task) { exit 0 }  # task removed, watchdog has nothing to manage

Write-Log "[RESTART] Heartbeat stale ${age}s (threshold ${StaleThresholdSeconds}s) -- restarting $CaptureTask"

Stop-ScheduledTask -TaskName $CaptureTask -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3
Start-ScheduledTask -TaskName $CaptureTask

Write-Log "[RESTART] $CaptureTask restarted."
Send-Toast "Coach Nono Watchdog" "Capture agent was stale (${age}s) and has been restarted."
