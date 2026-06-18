# Prints a snapshot of the Coach Nono data collection layer.
# Run from repo root: .\capture\status.ps1

$ROOT   = Split-Path $PSScriptRoot -Parent
$DATA   = Join-Path $ROOT "data"
$RAW    = Join-Path $DATA "raw"
$LAPS   = Join-Path $DATA "laps"
$STATUS = Join-Path $DATA "logs\capture\status.json"
$HR     = "-" * 52

function Write-Section($title) {
    Write-Host "`n$title" -ForegroundColor Yellow
}

function Format-GB($bytes) {
    "{0:N1} GB" -f ($bytes / 1GB)
}

# ── Header ──────────────────────────────────────────────
Write-Host ""
Write-Host "  Coach Nono — Data Layer Status" -ForegroundColor Cyan
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray
Write-Host "  $HR"

# ── Docker containers ────────────────────────────────────
Write-Section "  Containers"
$dockerOk = $false
try {
    $psOutput = docker compose ps --format "{{.Service}},{{.Status}}" 2>$null
    $dockerOk = $?
} catch { }

if ($dockerOk -and $psOutput) {
    $want = @("db", "ingest", "process", "dashboard-v2")
    $found = @{}
    $psOutput | ForEach-Object {
        $parts = $_ -split ",", 2
        if ($parts.Count -eq 2) { $found[$parts[0].Trim()] = $parts[1].Trim() }
    }
    foreach ($svc in $want) {
        $st = if ($found.ContainsKey($svc)) { $found[$svc] } else { "not found" }
        $color = if ($st -like "Up*" -or $st -like "running*") { "Green" }
                 elseif ($st -eq "not found")                   { "DarkGray" }
                 else                                            { "Red" }
        Write-Host ("    {0,-15} {1}" -f $svc, $st) -ForegroundColor $color
    }
} else {
    Write-Host "    Docker not reachable (is Docker Desktop running?)" -ForegroundColor Red
}

# ── Capture agent ────────────────────────────────────────
Write-Section "  Capture Agent"
if (Test-Path $STATUS) {
    try {
        $s   = Get-Content $STATUS -Raw | ConvertFrom-Json
        $age = [int](New-TimeSpan -Start ([datetime]$s.last_heartbeat) -End (Get-Date)).TotalSeconds
        if ($age -gt 15) {
            Write-Host "    STALE — last heartbeat ${age}s ago" -ForegroundColor Red
        } else {
            $stColor = switch ($s.state) {
                "live"        { "Green"  }
                "idle"        { "Gray"   }
                "paused_disk" { "Red"    }
                default       { "Yellow" }
            }
            Write-Host ("    State : ") -NoNewline
            Write-Host ($s.state.ToUpper()) -ForegroundColor $stColor
            if ($s.state -eq "live") {
                Write-Host "    Session : $($s.session_id)"
                Write-Host "    Lap     : $($s.current_lap)  (written this session: $($s.laps_written_session))"
            }
            Write-Host "    Total laps written : $($s.laps_written_total)"
            Write-Host "    Free disk          : $("{0:N1}" -f $s.free_disk_gb) GB"
            if ($s.sweep_trigger_active) {
                Write-Host "    [!] Disk-alert sweep pending" -ForegroundColor Red
            }
            Write-Host "    Heartbeat : ${age}s ago" -ForegroundColor DarkGray
        }
    } catch {
        Write-Host "    status.json unreadable: $_" -ForegroundColor Red
    }
} else {
    # Phase 0: no status.json yet — check for the process
    $running = $false
    try {
        $running = ($null -ne (
            Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction Stop |
            Where-Object { $_.CommandLine -like "*capture_agent*" }
        ))
    } catch { }
    if ($running) {
        Write-Host "    Running  (no status.json — Phase 1 logging not yet wired)" -ForegroundColor Yellow
    } else {
        Write-Host "    NOT running" -ForegroundColor Red
    }
}

# ── data/raw — pending ingest ────────────────────────────
Write-Section "  data/raw  (pending ingest)"
if (Test-Path $RAW) {
    $sessions  = @(Get-ChildItem $RAW -Directory -ErrorAction SilentlyContinue)
    $metas     = @($sessions | ForEach-Object { Get-ChildItem $_.FullName -Filter "*.meta.json" -ErrorAction SilentlyContinue })
    $parquets  = @($sessions | ForEach-Object { Get-ChildItem $_.FullName -Filter "*.parquet"   -ErrorAction SilentlyContinue })
    $rawBytes  = ($parquets | Measure-Object Length -Sum).Sum
    Write-Host ("    Sessions: {0}   Laps: {1}   Size: {2}" -f $sessions.Count, $metas.Count, (Format-GB $rawBytes))
    $sessions | Sort-Object Name | Select-Object -Last 3 | ForEach-Object {
        $n = @(Get-ChildItem $_.FullName -Filter "*.meta.json" -ErrorAction SilentlyContinue).Count
        Write-Host "      $($_.Name)  ($n laps)" -ForegroundColor DarkGray
    }
} else {
    Write-Host "    (directory not found)" -ForegroundColor DarkGray
}

# ── data/laps — processed ────────────────────────────────
Write-Section "  data/laps  (processed)"
if (Test-Path $LAPS) {
    $lapSess  = @(Get-ChildItem $LAPS -Directory -ErrorAction SilentlyContinue)
    $lapFiles = @($lapSess | ForEach-Object { Get-ChildItem $_.FullName -Filter "*.parquet" -ErrorAction SilentlyContinue })
    $lapBytes = ($lapFiles | Measure-Object Length -Sum).Sum
    Write-Host ("    Sessions: {0}   Laps: {1}   Size: {2}" -f $lapSess.Count, $lapFiles.Count, (Format-GB $lapBytes))
    $lapSess | Sort-Object Name | Select-Object -Last 3 | ForEach-Object {
        $n = @(Get-ChildItem $_.FullName -Filter "*.parquet" -ErrorAction SilentlyContinue).Count
        Write-Host "      $($_.Name)  ($n laps)" -ForegroundColor DarkGray
    }
} else {
    Write-Host "    (directory not found)" -ForegroundColor DarkGray
}

# ── Disk space ───────────────────────────────────────────
Write-Section "  Disk Space"
try {
    $drive   = [System.IO.DriveInfo]::new((Split-Path $DATA -Qualifier))
    $freeGB  = [math]::Round($drive.AvailableFreeSpace / 1GB, 1)
    $totalGB = [math]::Round($drive.TotalSize / 1GB, 1)
    $usedPct = [int](100 - ($drive.AvailableFreeSpace / $drive.TotalSize * 100))
    $diskColor = if ($freeGB -lt 5) { "Red" } elseif ($freeGB -lt 20) { "Yellow" } else { "Green" }
    Write-Host ("    {0:N1} GB free / {1:N1} GB total  ({2}% used)" -f $freeGB, $totalGB, $usedPct) -ForegroundColor $diskColor
} catch {
    Write-Host "    Could not read disk info" -ForegroundColor DarkGray
}

Write-Host ""
