# uninstall_service.ps1
# Stops and removes the CoachNono-Capture NSSM service.
# Does NOT delete logs or data.

$ErrorActionPreference = "Stop"
$ServiceName = "CoachNono-Capture"

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw "Must run as Administrator."
}

$nssmCmd = Get-Command nssm.exe -ErrorAction SilentlyContinue
if (-not $nssmCmd) {
    $local = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "nssm.exe"
    if (Test-Path $local) { $nssmCmd = @{ Source = $local } }
}
if (-not $nssmCmd) {
    throw "nssm.exe not found on PATH."
}
$nssm = $nssmCmd.Source

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $svc) {
    Write-Host "$ServiceName is not installed -- nothing to do."
    exit 0
}

if ($svc.Status -eq "Running") {
    Write-Host "Stopping $ServiceName..."
    Stop-Service -Name $ServiceName -Force
    Start-Sleep -Seconds 2
}

Write-Host "Removing $ServiceName..."
& $nssm remove $ServiceName confirm
Write-Host "Done. Logs remain in data\logs\capture\"
