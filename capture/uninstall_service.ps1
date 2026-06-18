# uninstall_service.ps1
# Stops and removes the CoachNono-Capture Scheduled Task.
# Does NOT delete logs or data.

$ErrorActionPreference = "Stop"
$TaskName     = "CoachNono-Capture"
$WatchdogTask = "CoachNono-Watchdog"

foreach ($t in @($TaskName, $WatchdogTask)) {
    $task = Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "$t is not registered -- skipping."
    } else {
        if ($task.State -eq "Running") {
            Write-Host "Stopping $t..."
            Stop-ScheduledTask -TaskName $t
            Start-Sleep -Seconds 2
        }
        Write-Host "Removing $t..."
        Unregister-ScheduledTask -TaskName $t -Confirm:$false
    }
}
Write-Host "Done. Logs remain in data\logs\capture\"

# Also clean up any lingering NSSM service (legacy, safe no-op if absent)
$svc = Get-Service -Name $TaskName -ErrorAction SilentlyContinue
if ($svc) {
    Write-Host "Removing legacy NSSM service $TaskName..."
    $nssm = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "nssm.exe"
    if (Test-Path $nssm) {
        & $nssm remove $TaskName confirm
    } else {
        sc.exe delete $TaskName | Out-Null
    }
    Write-Host "Legacy service removed."
}
