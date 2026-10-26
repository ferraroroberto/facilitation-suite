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
      4. static ratchet      - no new duplicate block (jscpd via npx) or unused
                               import/variable (ruff F401,F841), or ruff bug (B) /
                               security (S) finding (S113 strict), beyond
                               .static-ratchet-baseline.json. Whole tree, never
                               routed; exit 3 (a detector could not run) is
                               NOT CONFIRMED and fails, never a pass or a skip.
      5. docs ratchet        - README.md stays under 40 KiB and README.md /
                               docs/**/*.md gain no history marker (issue
                               reference, dated or changelog heading) beyond
                               .docs-ratchet-baseline.json. Whole tree, never
                               routed; exit 3 (docs unreadable) is NOT CONFIRMED
                               and fails.
      6. comment ratchet     - no file's count of `#NNN` issue references
                               inside comments rises beyond
                               .comment-ratchet-baseline.json. Whole tree, never
                               routed; exit 3 (a file could not be scanned) is
                               NOT CONFIRMED and fails.
      7. structure ratchet   - no new file over 1,000 lines, function over 100
                               lines or directory over 40 modules, no baselined
                               offender growing past
                               .structure-ratchet-baseline.json, and the root
                               folders match .fleet.toml shape + extra_roots.
                               Whole tree, never routed; exit 3 (git unusable,
                               an unparseable file, an unknown shape) is NOT
                               CONFIRMED and fails.
      8. instruction ratchet - no instruction file (CLAUDE.md, AGENTS.md, skills)
                               gains a prompt-audit rule hit or passes its size
                               cap beyond .instruction-ratchet-baseline.json.
                               Whole tree, never routed; exit 3 (git unusable, an
                               unreadable file or skill description) is NOT
                               CONFIRMED and fails.
      9. doc-refs ratchet    - README.md, CLAUDE.md and docs/**/*.md name no repo
                               path, `python -m` module, relative link or
                               loopback port that does not exist beyond
                               .doc-refs-ratchet-baseline.json. Whole tree, never
                               routed; exit 3 (git unusable, a file not UTF-8, an
                               unclosed fence) is NOT CONFIRMED and fails.
     10. dead-code ratchet   - no function, class or method that nothing in the
                               tracked Python tree refers to beyond
                               .dead-code-ratchet-baseline.json. Whole tree, never
                               routed; exit 3 (git unusable, a file that does not
                               parse) is NOT CONFIRMED and fails.
     11. conventions ratchet - no file's count of a fleet-convention hit (spawn
                               without CREATE_NO_WINDOW, print in library code,
                               hardcoded drive path) rises beyond
                               .conventions-ratchet-baseline.json. Whole tree,
                               never routed; exit 3 (git unusable, a file that
                               does not parse) is NOT CONFIRMED and fails.
     12. pytest (unit)       - hermetic suite, tests/e2e excluded
     13. pytest (e2e)       - diff-proportionate: routed by scripts/classify_e2e.py
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

. (Join-Path $PSScriptRoot "box_lock_gate.ps1"); Enter-BoxLockGate

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
        ($_ -match '(^|/)(chat|events|quiz)\.jsonl$' -and $_ -notmatch '^tests/fixtures/') -or
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
# The wrapper maps the script's exit 3 to NOT CONFIRMED (tests/test_static_ratchet_gate.py
# pins it); call it, never scripts/static_ratchet.py directly.
Invoke-Stage "static ratchet"          { & $py -m scripts.static_ratchet_gate }
# Same shape: the wrapper maps exit 3 to NOT CONFIRMED (tests/test_ratchet_gates.py).
Invoke-Stage "docs ratchet"            { & $py -m scripts.docs_ratchet_gate }
# Same shape: the wrapper maps exit 3 to NOT CONFIRMED (tests/test_ratchet_gates.py).
Invoke-Stage "comment ratchet"         { & $py -m scripts.comment_ratchet_gate }
# Called directly: Invoke-Stage fails on any nonzero exit, so the script's exit 3
# (NOT CONFIRMED) fails the gate like exit 1 and 2; no wrapper maps it.
Invoke-Stage "structure ratchet"       { & $py "scripts/structure_ratchet.py" }
# Called directly for the same reason: exit 3 (NOT CONFIRMED) fails the stage like exit 1 and 2.
Invoke-Stage "instruction ratchet"     { & $py "scripts/instruction_ratchet.py" }
# Called directly for the same reason: exit 3 (NOT CONFIRMED) fails the stage like exit 1 and 2.
Invoke-Stage "doc-refs ratchet"        { & $py "scripts/doc_refs_ratchet.py" }
# Called directly for the same reason: exit 3 (NOT CONFIRMED) fails the stage like exit 1 and 2.
Invoke-Stage "dead-code ratchet"       { & $py "scripts/dead_code_ratchet.py" }
# Called directly for the same reason: exit 3 (NOT CONFIRMED) fails the stage like exit 1 and 2.
Invoke-Stage "conventions ratchet"     { & $py "scripts/conventions_ratchet.py" }
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
    # The classifier joins several routed modules with spaces; pytest needs each as its own argument.
    $e2eArgs = @($e2eTarget -split '\s+' | Where-Object { $_ })
    foreach ($b in ($e2eBrowsers -split ',' | Where-Object { $_ })) {
        $e2eArgs += @("--browser", $b)
    }
    # The timing record /e2e-audit reads ([e2e] junit_xml in .fleet.toml); data/ is gitignored.
    $e2eArgs += @("--junitxml", "data/e2e-junit.xml")
    Invoke-Stage "pytest e2e (${tier}: $e2eTarget)" { & $py -m pytest @e2eArgs }
}

Write-Host ""
Write-Host "[PASS] all checks green - safe to ship." -ForegroundColor Green
