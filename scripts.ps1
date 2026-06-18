# List all project scripts with descriptions. Run by the desktop shortcut on open.

function Write-Group($title, $entries) {
    $rule = "-" * ([Math]::Max(2, 58 - $title.Length))
    Write-Host "  -- $title $rule" -ForegroundColor DarkGray
    foreach ($e in $entries) {
        Write-Host ("    {0,-42}" -f $e.Path) -NoNewline -ForegroundColor Yellow
        Write-Host $e.Desc -ForegroundColor DarkGray
    }
    Write-Host ""
}

Write-Host ""
Write-Host "  Coach Nono -- Project Scripts" -ForegroundColor Cyan
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host ""

$capture = @(
    [PSCustomObject]@{ Path = "capture\run_capture.ps1";  Desc = "Start the telemetry capture agent (50 Hz, Win32 shared memory)" },
    [PSCustomObject]@{ Path = "capture\status.ps1";       Desc = "Snapshot: containers, capture state, pending laps, disk space" },
    [PSCustomObject]@{ Path = "capture\watch_laps.ps1";   Desc = "Live lap feed - watches for new laps as they are written (Ctrl+C to stop)" },
    [PSCustomObject]@{ Path = "capture\capture_agent.py"; Desc = "Capture agent source - run via run_capture.ps1, not directly" }
)

$root = @(
    [PSCustomObject]@{ Path = "scripts.ps1"; Desc = "This file - list all project scripts (regenerate with /nono-desktop-shortcut)" }
)

Write-Group "Capture" $capture
Write-Group "Root"    $root

Write-Host "  Run any script from the project root: " -NoNewline -ForegroundColor DarkGray
Write-Host ".\" -NoNewline -ForegroundColor White
Write-Host "<path>" -ForegroundColor White
Write-Host ""
