#Requires -Version 5.1
<#
.SYNOPSIS
    Pre-ship verification gate. One pass/fail pipeline, fail-fast.

.DESCRIPTION
    Stages:
      1. byte-compile      - every .py under app/ src/ tests/ parses
      2. pytest            - the unit suite (invented fixtures only), bracketed by the runtime
                             write probe (scripts/runtime_write_ratchet.py --snapshot
                             before, --probe after): any untracked or ignored file the
                             tests left under a code folder fails
         e2e               - tests/e2e in Chromium (desktop) and WebKit (iPhone) against a
                             disposable app booted on a free port over an invented tree;
                             a boot failure fails, never skips
      3. structure ratchet - scripts/structure_ratchet.py: file size, function
                             length, flat directories, the src -> app layer
                             contract and the root layout against the baseline;
                             an exit 3 ("not confirmed") is a failure, never a pass
                             (docs/layout.md)
      4. instruction ratchet - scripts/instruction_ratchet.py: the prompt-audit lint
                             rules and size caps over CLAUDE.md / AGENTS.md /
                             skills against the baseline; exit 3 is a failure
      5. doc refs ratchet    - scripts/doc_refs_ratchet.py: README.md / CLAUDE.md / docs
                             naming a path, module, link or port that does not exist,
                             against the baseline; exit 3 is a failure
      6. dead code ratchet   - scripts/dead_code_ratchet.py: Python functions, classes and
                             methods nothing refers to, against the baseline;
                             an exit 3 is a failure
      7. static ratchet      - scripts/static_ratchet.py: duplicated blocks (jscpd, needs
                             Node), unused imports/variables and likely-bug/security
                             patterns (ruff) against the baseline; exit 3 is a failure
      8. docs ratchet        - scripts/docs_ratchet.py: README.md size and the history
                             markers (issue references, dated and changelog headings) in
                             README.md / docs against the baseline; exit 3 is a failure
      9. readme index ratchet - scripts/readme_index_ratchet.py (repo-specific, not
                             vendored): every README.md top-level feature bullet's word
                             count against the baseline -- a bullet may not regrow into a
                             reference paragraph, and a brand-new bullet must fit the index
                             rule on its own; exit 3 is a failure
     10. comment ratchet     - scripts/comment_ratchet.py: issue references in product-code
                             comments (Python, JS, CSS, PowerShell) against the baseline;
                             an exit 3 is a failure
     11. conventions ratchet - scripts/conventions_ratchet.py: the no-window,
                             logging-not-print, no-drive-path, secret-compare,
                             git-wrapper and password-input-vendored rules against the
                             baseline; exit 3 is a failure
     12. runtime write ratchet - scripts/runtime_write_ratchet.py: a write target resolving
                             under a code folder against the baseline; a new unresolvable
                             target is exit 3, a failure
     13. usage track ratchet - scripts/usage_track_ratchet.py: a web control with no constant
                             tracking id, a <dialog> with no data-track-view, a built id or
                             an unwired usage log against the baseline; exit 3 is a failure
     14. UX surface ratchet  - scripts/ux_surface_ratchet.py: a surface root with no constant
                             data-surface id, an id with no entry in docs/ux-surfaces.yaml, an
                             entry no element carries, an id on two surfaces, a link to an id
                             with no entry, and a docs/ux-surface-map.md page that is not what
                             the map generates, against the baseline; exit 3 is a failure
     15. request budget ratchet - scripts/request_budget_ratchet.py: a fixed-rate browser poll
                             that reaches the server, a poll under 2 s, an undeclared fast server
                             loop, a directory walk in a request handler, a submit to the shared
                             default pool or a web app without the request-timing middleware,
                             against the baseline; exit 3 is a failure
     16. install standard ratchet - scripts/install_standard_ratchet.py: an installer module
                             missing, undeclared or behind the scaffold's, an [install.migrate]
                             that does not parse, or docs/install.md without its Install, Update
                             or Migrate section, against the baseline; exit 3 is a failure

    Verdict line: the last line on stdout is `GATE-RESULT: PASS` or
    `GATE-RESULT: FAIL`, on every path that can still run code. Never pipe the
    gate when its exit code matters (a pipeline reports its last command's
    status, so `... | tail -40` turns a red run green); a caller that cannot
    avoid a pipe asserts on that line. A run with no such line is not a pass.

    Anchors to the repo root, so run it from anywhere:
        powershell -File scripts/verify-before-ship.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

# gate-verdict:begin
# The verdict is the last stdout line of every path that can still run code. A
# terminating error under the "Stop" preference above would otherwise print to
# stderr only and exit 1, leaving a piped caller an empty, apparently clean run,
# so the trap reports it on stdout first. Exit codes stay 0 (pass) and 1 (fail).
function Write-GateResult($verdict) {
    Write-Host "GATE-RESULT: $verdict"
}

function Complete-Gate([int]$Code) {
    Write-GateResult $(if ($Code -eq 0) { "PASS" } else { "FAIL" })
    exit $Code
}

trap {
    Write-Host ""
    Write-Host "[FAIL] the gate aborted on an unhandled error:" -ForegroundColor Red
    Write-Host ("       {0}" -f $_.Exception.Message) -ForegroundColor Red
    Complete-Gate 1
}

function Invoke-Stage {
    param([string]$Name, [scriptblock]$Body)
    Write-Host ""
    Write-Host ">> $Name" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[FAIL] $Name (exit $LASTEXITCODE)" -ForegroundColor Red
        Complete-Gate 1
    }
    Write-Host "[PASS] $Name" -ForegroundColor Green
}
# gate-verdict:end

Set-Location (Join-Path $PSScriptRoot "..")

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[FAIL] .venv not found at $py" -ForegroundColor Red
    Write-Host "       Create it and run: $py -m pip install -r requirements-dev.txt" -ForegroundColor Red
    Complete-Gate 1
}

# Box load lock: re-runs this script under the shared lock and exits with its
# code. The gate itself only ever exits 0 or 1, so the helper's exit 3 ("lock
# not taken in time") cannot be confused with it. Loaded as text, not dot-sourced
# from its file: under an AllSigned policy a dot-sourced unsigned file is refused,
# while a ScriptBlock made from its text runs with the policy unchanged.
. ([scriptblock]::Create([IO.File]::ReadAllText((Join-Path $PSScriptRoot "box_lock_gate.ps1")))); Enter-BoxLockGate

Invoke-Stage "byte-compile"      { & $py -m compileall -q app src tests }
# Runtime write probe, first half: what already sits untracked or ignored under
# the code folders, so the probe after the tests names only what they wrote.
$runtimeSnapshot = Join-Path ([IO.Path]::GetTempPath()) "runtime-write-snapshot-$PID.json"
Invoke-Stage "runtime write probe (snapshot)" { & $py "scripts/runtime_write_ratchet.py" --snapshot $runtimeSnapshot }
Invoke-Stage "pytest"            { & $py -m pytest -q tests --ignore=tests/e2e }
$env:FOLDER_EXPLORER_E2E_AUTOBOOT = "1"
# The per-test seconds /e2e-audit reads (.fleet.toml [e2e] junit_xml), overwritten each run.
Invoke-Stage "e2e"               { & $py -m pytest -q tests/e2e --junitxml ".pytest_cache/e2e-junit.xml" }
# The Mac nightly runs the request-budget tests on WebKit. 0 passes; 4 (WebKit not
# installed) and 5 (no budget test) are "not run", printed as such and never as a
# pass; anything else, 3 (not confirmed) included, fails the gate.
Write-Host ""
Write-Host ">> request budget on webkit" -ForegroundColor Cyan
& $py "scripts/tools/webkit_budget_check.py"
$webkitBudget = $LASTEXITCODE
if ($webkitBudget -eq 0) {
    Write-Host "[PASS] request budget on webkit" -ForegroundColor Green
} elseif ($webkitBudget -eq 4) {
    Write-Host "[NOT RUN] request budget on webkit - WebKit is not installed here, so the nightly's engine was not checked" -ForegroundColor Yellow
} elseif ($webkitBudget -eq 5) {
    Write-Host "[NOT RUN] request budget on webkit - no request-budget test in this repo" -ForegroundColor Yellow
} else {
    Write-Host "[FAIL] request budget on webkit (exit $webkitBudget)" -ForegroundColor Red
    Complete-Gate 1
}
Invoke-Stage "runtime write probe" { & $py "scripts/runtime_write_ratchet.py" --probe $runtimeSnapshot }
Invoke-Stage "structure ratchet" { & $py "scripts/structure_ratchet.py" }
Invoke-Stage "instruction ratchet" { & $py "scripts/instruction_ratchet.py" }
Invoke-Stage "doc refs ratchet" { & $py "scripts/doc_refs_ratchet.py" }
Invoke-Stage "dead code ratchet" { & $py "scripts/dead_code_ratchet.py" }
Invoke-Stage "static ratchet" { & $py "scripts/static_ratchet.py" }
Invoke-Stage "docs ratchet" { & $py "scripts/docs_ratchet.py" }
Invoke-Stage "readme index ratchet" { & $py "scripts/readme_index_ratchet.py" }
Invoke-Stage "comment ratchet" { & $py "scripts/comment_ratchet.py" }
Invoke-Stage "conventions ratchet" { & $py "scripts/conventions_ratchet.py" }
Invoke-Stage "runtime write ratchet" { & $py "scripts/runtime_write_ratchet.py" }
Invoke-Stage "usage track ratchet" { & $py "scripts/usage_track_ratchet.py" }
Invoke-Stage "ux surface ratchet" { & $py "scripts/ux_surface_ratchet.py" }
Invoke-Stage "request budget ratchet" { & $py "scripts/request_budget_ratchet.py" }
Invoke-Stage "install standard ratchet" { & $py "scripts/install_standard_ratchet.py" }

Write-Host ""
Write-Host "[PASS] all checks green - safe to ship." -ForegroundColor Green
Complete-Gate 0
