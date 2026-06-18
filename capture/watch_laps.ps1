# Watches for new laps written by the capture agent, similar to the capture console.
# Phase 1+: tails data/logs/capture/capture.log (JSON lines, formatted)
# Now     : polls data/raw/ for new .meta.json files at 500 ms
# Run from repo root: .\capture\watch_laps.ps1

param(
    [switch]$Raw   # show raw JSON lines even in Phase 1 mode
)

$ROOT = Split-Path $PSScriptRoot -Parent
$DATA = Join-Path $ROOT "data"
$RAW  = Join-Path $DATA "raw"
$LOG  = Join-Path $DATA "logs\capture\capture.log"

Write-Host ""
Write-Host "  Coach Nono — Lap Watch  (Ctrl+C to stop)" -ForegroundColor Cyan
Write-Host ""

# ── Phase 1+ mode: tail the structured log file ──────────
if ((Test-Path $LOG) -and -not $Raw) {
    Write-Host "  Tailing $LOG" -ForegroundColor DarkGray
    Write-Host ""

    # Start from the end of the file
    $stream = [System.IO.File]::Open($LOG, 'Open', 'Read', 'ReadWrite')
    $stream.Seek(0, 'End') | Out-Null
    $reader = [System.IO.StreamReader]::new($stream)

    try {
        while ($true) {
            $line = $reader.ReadLine()
            if ($null -eq $line) {
                Start-Sleep -Milliseconds 200
                continue
            }
            if (-not $line.Trim()) { continue }

            if ($Raw) {
                Write-Host $line
                continue
            }

            try {
                $e     = $line | ConvertFrom-Json
                $ts    = if ($e.ts)        { $e.ts }        `
                         elseif ($e.time)  { $e.time }      `
                         else              { "" }
                $level = if ($e.level)     { $e.level }     `
                         elseif ($e.lvl)   { $e.lvl }       `
                         else              { "INFO" }
                $msg   = if ($e.msg)       { $e.msg }       `
                         elseif ($e.message) { $e.message } `
                         else              { $line }

                $color = switch ($level.ToUpper()) {
                    "ERROR"   { "Red"     }
                    "WARNING" { "Yellow"  }
                    "INFO"    { "White"   }
                    default   { "Gray"    }
                }

                # Lap-written lines get extra formatting
                if ($msg -match "Lap \d+") {
                    Write-Host ("  {0}  " -f $ts) -NoNewline -ForegroundColor DarkGray
                    Write-Host $msg -ForegroundColor $color
                } else {
                    Write-Host ("  {0}  {1,-8}  {2}" -f $ts, $level, $msg) -ForegroundColor $color
                }
            } catch {
                Write-Host "  $line" -ForegroundColor Gray
            }
        }
    } finally {
        $reader.Dispose()
        $stream.Dispose()
    }
}

# ── Fallback mode: poll data/raw/ for new meta.json files ─
Write-Host "  Watching data/raw/ for new laps (Phase 1 log not found)" -ForegroundColor DarkGray
Write-Host "  Tip: run .\capture\run_capture.ps1 in another window to start capture" -ForegroundColor DarkGray
Write-Host ""

$seen      = @{}
$lastSess  = $null

# Seed with already-existing files so only new laps appear
if (Test-Path $RAW) {
    Get-ChildItem $RAW -Recurse -Filter "*.meta.json" -ErrorAction SilentlyContinue |
        ForEach-Object { $seen[$_.FullName] = $true }
}

while ($true) {
    Start-Sleep -Milliseconds 500

    if (-not (Test-Path $RAW)) { continue }

    Get-ChildItem $RAW -Recurse -Filter "*.meta.json" -ErrorAction SilentlyContinue |
        Where-Object { -not $seen.ContainsKey($_.FullName) } |
        Sort-Object LastWriteTime |
        ForEach-Object {
            $seen[$_.FullName] = $true
            try {
                $m    = Get-Content $_.FullName -Raw | ConvertFrom-Json
                $lapS = $m.lap_time_ms / 1000.0
                $min  = [int]($lapS / 60)
                $sec  = $lapS % 60
                $ts   = Get-Date -Format "HH:mm:ss"

                # Print session header when session changes
                if ($m.session_id -ne $lastSess) {
                    $lastSess = $m.session_id
                    Write-Host ""
                    Write-Host ("  Session: {0}" -f $m.session_id) -ForegroundColor Cyan
                    Write-Host ("  Car: {0}   Track: {1}   Type: {2}" -f $m.car, $m.track, $m.session_type) -ForegroundColor DarkGray
                    Write-Host ""
                }

                $valid      = if ($m.valid) { "VALID  " } else { "INVALID" }
                $validColor = if ($m.valid) { "Green"   } else { "Yellow"  }
                $lapNum     = $m.lap_index + 1

                Write-Host ("  {0}  " -f $ts) -NoNewline -ForegroundColor DarkGray
                Write-Host ("Lap {0,-3}" -f $lapNum) -NoNewline
                Write-Host ("  {0}:{1:06.3f}" -f $min, $sec) -NoNewline -ForegroundColor White
                Write-Host ("  {0}" -f $valid) -NoNewline -ForegroundColor $validColor
                Write-Host ("  {0} samples" -f $m.sample_count) -ForegroundColor DarkGray
            } catch {
                Write-Host "  New lap: $($_.Name)" -ForegroundColor DarkGray
            }
        }
}
