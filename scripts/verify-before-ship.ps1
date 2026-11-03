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
                             exit 3 ("not confirmed") is a failure, never a pass
                             (docs/layout.md)
      4. instruction ratchet - scripts/instruction_ratchet.py: the prompt-audit lint
                             rules and size caps over CLAUDE.md / AGENTS.md /
                             skills against the baseline; exit 3 is a failure
      5. doc refs ratchet    - scripts/doc_refs_ratchet.py: README.md / CLAUDE.md / docs
                             naming a path, module, link or port that does not exist,
                             against the baseline; exit 3 is a failure
      6. dead code ratchet   - scripts/dead_code_ratchet.py: Python functions, classes and
                             methods nothing refers to, against the baseline;
                             exit 3 is a failure
      7. static ratchet      - scripts/static_ratchet.py: duplicated blocks (jscpd, needs
                             Node), unused imports/variables and likely-bug/security
                             patterns (ruff) against the baseline; exit 3 is a failure
      8. docs ratchet        - scripts/docs_ratchet.py: README.md size and the history
                             markers (issue references, dated and changelog headings) in
                             README.md / docs against the baseline; exit 3 is a failure
      9. comment ratchet     - scripts/comment_ratchet.py: issue references in product-code
                             comments (Python, JS, CSS, PowerShell) against the baseline;
                             exit 3 is a failure
     10. conventions ratchet - scripts/conventions_ratchet.py: the no-window,
                             logging-not-print, no-drive-path, secret-compare,
                             git-wrapper and password-input-vendored rules against the
                             baseline; exit 3 is a failure
     11. runtime write ratchet - scripts/runtime_write_ratchet.py: a write target resolving
                             under a code folder against the baseline; a new unresolvable
                             target is exit 3, a failure
     12. usage track ratchet - scripts/usage_track_ratchet.py: a web control with no constant
                             tracking id, a <dialog> with no data-track-view, a built id or
                             an unwired usage log against the baseline; exit 3 is a failure

    Anchors to the repo root, so run it from anywhere:
        powershell -File scripts/verify-before-ship.ps1
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[FAIL] .venv not found at $py" -ForegroundColor Red
    Write-Host "       Create it and run: $py -m pip install -r requirements-dev.txt" -ForegroundColor Red
    exit 1
}

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

Invoke-Stage "byte-compile"      { & $py -m compileall -q app src tests }
# Runtime write probe, first half: what already sits untracked or ignored under
# the code folders, so the probe after the tests names only what they wrote.
$runtimeSnapshot = Join-Path ([IO.Path]::GetTempPath()) "runtime-write-snapshot-$PID.json"
Invoke-Stage "runtime write probe (snapshot)" { & $py "scripts/runtime_write_ratchet.py" --snapshot $runtimeSnapshot }
Invoke-Stage "pytest"            { & $py -m pytest -q tests --ignore=tests/e2e }
$env:FOLDER_EXPLORER_E2E_AUTOBOOT = "1"
Invoke-Stage "e2e"               { & $py -m pytest -q tests/e2e }
Invoke-Stage "runtime write probe" { & $py "scripts/runtime_write_ratchet.py" --probe $runtimeSnapshot }
Invoke-Stage "structure ratchet" { & $py "scripts/structure_ratchet.py" }
Invoke-Stage "instruction ratchet" { & $py "scripts/instruction_ratchet.py" }
Invoke-Stage "doc refs ratchet" { & $py "scripts/doc_refs_ratchet.py" }
Invoke-Stage "dead code ratchet" { & $py "scripts/dead_code_ratchet.py" }
Invoke-Stage "static ratchet" { & $py "scripts/static_ratchet.py" }
Invoke-Stage "docs ratchet" { & $py "scripts/docs_ratchet.py" }
Invoke-Stage "comment ratchet" { & $py "scripts/comment_ratchet.py" }
Invoke-Stage "conventions ratchet" { & $py "scripts/conventions_ratchet.py" }
Invoke-Stage "runtime write ratchet" { & $py "scripts/runtime_write_ratchet.py" }
Invoke-Stage "usage track ratchet" { & $py "scripts/usage_track_ratchet.py" }

Write-Host ""
Write-Host "[PASS] all checks green - safe to ship." -ForegroundColor Green
