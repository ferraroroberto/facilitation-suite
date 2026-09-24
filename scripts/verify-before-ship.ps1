#Requires -Version 5.1
<#
.SYNOPSIS
    Pre-ship verification gate for facilitation-suite. One pass/fail pipeline.

.DESCRIPTION
    Stages, fail-fast:
      1. personal-data guard - no tracked roster/deck/ledger/config/session file
                               outside tests/fixtures (the repo is public)
      2. byte-compile        - every .py under app/ src/ scripts/ tests/ + launcher.py
      3. ruff                - lint the whole repo (pyproject.toml owns strictness)
      4. pytest (unit)       - hermetic suite, tests/e2e excluded
      5. pytest (e2e)        - diff-proportionate: routed by scripts/classify_e2e.py
                               against .fleet.toml [e2e] (skip / static / full),
                               fail-safe to full. Boots its own disposable webapp;
                               never the live :8449.

    Run from anywhere:  & .\scripts\verify-before-ship.ps1
    Restarting the live app afterwards is a separate step (CLAUDE.md "Restart
    recipe"): tray.bat --restart, then /api/version git_sha == HEAD.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[FAIL] .venv not found at $py" -ForegroundColor Red
    Write-Host "       Create it: py -m venv .venv; then $py -m pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Invoke-Stage {
    param([string]$Name, [scriptblock]$Body)
    Write-Host ""
    Write-Host ">> $Name" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] $Name (exit $LASTEXITCODE)" -ForegroundColor Red
        exit 1
    }
    Write-Host "[PASS] $Name" -ForegroundColor Green
}

Invoke-Stage "personal-data guard" {
    $tracked = git ls-files
    $bad = $tracked | Where-Object {
        ($_ -match '\.(xlsx|pptx|docx)$' -and $_ -notmatch '^tests/fixtures/') -or
        ($_ -match '(^|/)session\.yaml$' -and $_ -notmatch '^tests/fixtures/') -or
        ($_ -match '(^|/)(chat|events)\.jsonl$' -and $_ -notmatch '^tests/fixtures/') -or
        ($_ -eq 'sessions.local.yaml') -or
        ($_ -eq 'config/config.json') -or
        ($_ -match '^data/') -or
        ($_ -eq '.env')
    }
    if ($bad) {
        Write-Host "tracked personal/machine files:" -ForegroundColor Red
        $bad | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
        $global:LASTEXITCODE = 1
    } else {
        $global:LASTEXITCODE = 0
    }
}
Invoke-Stage "byte-compile"            { & $py -m compileall -q app src scripts tests launcher.py }
Invoke-Stage "ruff"                    { & $py -m ruff check . }
Invoke-Stage "pytest (unit, non-e2e)"  { & $py -m pytest --ignore=tests/e2e }

# ---------------------------------------------------------------- e2e routing
$tier = "full"; $e2eTarget = "tests/e2e"; $e2eBrowsers = ""; $routeReason = ""
$classifyOut = & $py "scripts/classify_e2e.py"
$kv = @{}
foreach ($line in $classifyOut) {
    if ($line -match '^(E2E_[A-Z_]+)=(.*)$') { $kv[$matches[1]] = $matches[2] }
}
if ($kv.ContainsKey("E2E_TIER") -and $kv["E2E_TIER"]) {
    $tier = $kv["E2E_TIER"]
    $e2eTarget = $kv["E2E_PYTEST_TARGET"]
    $e2eBrowsers = $kv["E2E_BROWSERS"]
    $routeReason = $kv["E2E_REASON"]
} else {
    $routeReason = "classifier gave no verdict -- defaulting to full (fail-safe)"
}

if ($tier -eq "skip") {
    Write-Host ""
    Write-Host ">> e2e routing: SKIP browser suite (no e2e surface touched)" -ForegroundColor Cyan
    Write-Host "   reason: $routeReason" -ForegroundColor DarkGray
    Write-Host "[PASS] pytest (e2e) - skipped, diff touches no e2e surface" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host ">> e2e routing: $tier" -ForegroundColor Cyan
    Write-Host "   reason: $routeReason" -ForegroundColor DarkGray
    $e2eArgs = @($e2eTarget)
    foreach ($b in ($e2eBrowsers -split ',' | Where-Object { $_ })) {
        $e2eArgs += @("--browser", $b)
    }
    Invoke-Stage "pytest e2e (${tier}: $e2eTarget)" { & $py -m pytest @e2eArgs }
}

Write-Host ""
Write-Host "[PASS] all checks green - safe to ship." -ForegroundColor Green
