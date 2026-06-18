# install_service.ps1
# Registers the Coach Nono capture agent as a Windows Scheduled Task.
#
# Runs at logon in the current user's interactive session -- no stored password
# required. This avoids the Microsoft Account credential rejection that affects
# NSSM-based Windows Services on Windows 11.
#
# The task is registered with Start=Disabled for Phase 2 validation.
# After confirming capture works, promote to auto-start (Phase 3):
#   Get-ScheduledTask CoachNono-Capture | Start-ScheduledTask   # manual start
#   # Phase 3: task trigger is already AtLogon -- no further change needed.

$ErrorActionPreference = "Stop"
$TaskName  = "CoachNono-Capture"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = Split-Path -Parent $ScriptDir
$PythonW   = Join-Path $ScriptDir "venv\Scripts\pythonw.exe"
$Agent     = Join-Path $ScriptDir "capture_agent.py"
$LogDir    = Join-Path $RepoRoot  "data\logs\capture"

if (-not (Test-Path $PythonW)) {
    throw "pythonw.exe not found at:`n  $PythonW`nRun run_capture.ps1 once first to create the venv."
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# Remove existing task if present
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction `
    -Execute    $PythonW `
    -Argument   "`"$Agent`"" `
    -WorkingDirectory $ScriptDir

# AtLogon for the current user -- runs in the interactive session, no password stored
$trigger = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -MultipleInstances    IgnoreNew `
    -ExecutionTimeLimit   ([TimeSpan]::Zero) `
    -RestartCount         999 `
    -RestartInterval      (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable

$principal = New-ScheduledTaskPrincipal `
    -UserId    $env:USERNAME `
    -LogonType Interactive `
    -RunLevel  Highest

Register-ScheduledTask `
    -TaskName  $TaskName `
    -Action    $action `
    -Trigger   $trigger `
    -Settings  $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Task registered: $TaskName"

# ---------------------------------------------------------------------------
# Watchdog task: runs every 5 min, restarts capture if heartbeat goes stale
# ---------------------------------------------------------------------------
$WatchdogTask = "CoachNono-Watchdog"
$Watchdog     = Join-Path $ScriptDir "watchdog.ps1"
$PS           = (Get-Command powershell.exe).Source

Unregister-ScheduledTask -TaskName $WatchdogTask -Confirm:$false -ErrorAction SilentlyContinue

$wdAction = New-ScheduledTaskAction `
    -Execute  $PS `
    -Argument "-NonInteractive -WindowStyle Hidden -File `"$Watchdog`"" `
    -WorkingDirectory $ScriptDir

# Repeat every 5 minutes indefinitely
$wdTrigger = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME
$wdRepeat  = New-TimeSpan -Minutes 5
$wdTrigger.Repetition = (New-ScheduledTaskTrigger -RepetitionInterval $wdRepeat -Once -At (Get-Date)).Repetition

$wdSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::FromMinutes(2)) `
    -MultipleInstances  IgnoreNew `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName  $WatchdogTask `
    -Action    $wdAction `
    -Trigger   $wdTrigger `
    -Settings  $wdSettings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Task registered: $WatchdogTask (fires every 5 min, restarts capture if heartbeat stale >45s)"
Write-Host ""
# ---------------------------------------------------------------------------
# Tray icon task: pure viewer, starts at logon alongside capture
# ---------------------------------------------------------------------------
$TrayTask = "CoachNono-Tray"
$Tray     = Join-Path $ScriptDir "tray_icon.py"

Unregister-ScheduledTask -TaskName $TrayTask -Confirm:$false -ErrorAction SilentlyContinue

$trayAction = New-ScheduledTaskAction `
    -Execute          $PythonW `
    -Argument         "`"$Tray`"" `
    -WorkingDirectory $ScriptDir

$trayTrigger  = New-ScheduledTaskTrigger -AtLogon -User $env:USERNAME
$traySettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances  IgnoreNew

Register-ScheduledTask `
    -TaskName  $TrayTask `
    -Action    $trayAction `
    -Trigger   $trayTrigger `
    -Settings  $traySettings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Task registered: $TrayTask (system tray viewer, green/amber/red)"
Write-Host ""
Write-Host "All three tasks auto-start at next logon."
Write-Host ""
Write-Host "To stop:      Stop-ScheduledTask $TaskName; Stop-ScheduledTask $WatchdogTask; Stop-ScheduledTask $TrayTask"
Write-Host "To uninstall: .\uninstall_service.ps1"
