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

Write-Host ""
Write-Host "Task registered: $TaskName"
Write-Host "Trigger: At logon for $env:USERNAME (interactive session -- no password needed)"
Write-Host "Restart: every 1 min on crash, up to 999 times"
Write-Host ""
Write-Host "VALIDATION STEPS"
Write-Host "  1. Start the task manually:"
Write-Host "       Start-ScheduledTask $TaskName"
Write-Host "  2. Confirm idle (ACC not running):"
Write-Host "       Get-ScheduledTask $TaskName | Select-Object State"
Write-Host "       Get-Content '$LogDir\service-stdout.log' -Tail 20   # or capture.log"
Write-Host "       Get-Content '$LogDir\status.json'"
Write-Host "  3. Open ACC, go on track, confirm laps appear in data\raw\"
Write-Host "  4. Lockfile check: run .\run_capture.ps1 while task is running"
Write-Host "       -> should exit 'task already running'"
Write-Host ""
Write-Host "The task auto-starts at next logon (Phase 3 is already wired -- no extra command needed)."
Write-Host ""
Write-Host "To stop:      Stop-ScheduledTask $TaskName"
Write-Host "To uninstall: .\uninstall_service.ps1"
