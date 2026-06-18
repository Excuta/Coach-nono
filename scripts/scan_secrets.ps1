# scan_secrets.ps1 — pre-publication PII and secret scan. Read-only; safe to re-run.
# Produces a timestamped report in scan-reports\.

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$reportDir = Join-Path $repo 'scan-reports'
New-Item -ItemType Directory -Force $reportDir | Out-Null
$stamp   = Get-Date -Format 'yyyyMMdd-HHmmss'
$jsonOut = Join-Path $reportDir "gitleaks-$stamp.json"
$txtOut  = Join-Path $reportDir "scan-report-$stamp.txt"

$gitleaks = (Get-Command gitleaks -ErrorAction SilentlyContinue).Source
if (-not $gitleaks) { $gitleaks = Join-Path $env:USERPROFILE 'bin\gitleaks.exe' }

function Tee-Report { param([string]$Line) $Line | Tee-Object -FilePath $txtOut -Append }

"" | Set-Content $txtOut
Tee-Report "=== Coach Nono pre-publication scan ($stamp) ==="
Tee-Report "Repo: $repo"
Tee-Report ("Commits: " + (git rev-list --count HEAD))
Tee-Report ""

Tee-Report "--- [1] Gitleaks full-history secret scan ---"
if (Test-Path $gitleaks) {
    & $gitleaks detect --source . --log-opts "--all" -f json -r $jsonOut --no-banner
    $code = $LASTEXITCODE
    if (Test-Path $jsonOut) {
        $findings = Get-Content $jsonOut -Raw | ConvertFrom-Json
        Tee-Report ("Gitleaks findings: " + @($findings).Count + "  (exit $code)")
        foreach ($f in $findings) {
            Tee-Report ("  [{0}] {1}:{2}  rule={3}" -f $f.Commit.Substring(0,7), $f.File, $f.StartLine, $f.RuleID)
        }
    } else { Tee-Report "No findings or error. Exit $code." }
    Tee-Report "Raw JSON: $jsonOut"
} else { Tee-Report "WARNING: gitleaks not found. See plan pre-flight for install instructions." }
Tee-Report ""

Tee-Report "--- [2] Supplemental PII scan across full history ---"
$piiPatterns = @(
    @{ Name = 'Windows username (YOUR_USERNAME)';         Pattern = 'YOUR_USERNAME' },
    @{ Name = 'Personal email (<YOUR_EMAIL>)'; Pattern = 'YOUR_USERNAME5@live\.com' },
    @{ Name = 'Any C:\Users\<user> path';          Pattern = 'C:\\\\Users\\\\[^\\\\]+' },
    @{ Name = 'Any email address';                 Pattern = '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' }
)
foreach ($p in $piiPatterns) {
    Tee-Report ("Pattern: {0}" -f $p.Name)
    $hits = git grep -n -I -E $p.Pattern $(git rev-list --all) 2>$null
    if ($hits) { $hits | ForEach-Object { Tee-Report "    $_" } }
    else        { Tee-Report "    (no matches)" }
    Tee-Report ""
}

Tee-Report "--- [3] Commit identities ---"
git log --all --format='%ae | %an' | Sort-Object -Unique | ForEach-Object { Tee-Report "    $_" }
Tee-Report ""

Tee-Report "--- [4] Tracked files of interest ---"
git ls-files | Where-Object { $_ -match '(?i)(\.env|\.local|secret|credential|\.pem|\.key)$|settings\.local' } |
    ForEach-Object { Tee-Report "    TRACKED: $_" }
Tee-Report "Untracked machine-local files on disk:"
git status --porcelain --untracked-files=all |
    Where-Object { $_ -match 'settings\.local|\.env' } |
    ForEach-Object { Tee-Report "    $_" }
Tee-Report ""
Tee-Report "=== Scan complete. Report: $txtOut ==="
Write-Host "`nReport written to $txtOut" -ForegroundColor Cyan
