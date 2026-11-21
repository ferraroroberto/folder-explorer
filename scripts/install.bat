@echo off
REM ============================================================================
REM  folder-explorer INSTALL - set this checkout up on a PC, keeping its settings
REM ----------------------------------------------------------------------------
REM  Runs app\install.py (docs\install.md): creates .venv, creates config.json and
REM  .env from their templates only when missing (an existing config only gains
REM  the keys it lacks, after a backup), installs the requirements, and prints a
REM  checklist. Safe to run again: a second run reports unchanged.
REM
REM    scripts\install.bat              set up, or bring a set-up checkout in step
REM    scripts\install.bat -WhatIf      list every change, make none
REM    scripts\install.bat --startup    also start the tray at sign-in
REM    scripts\install.bat --uninstall  remove only the Startup shortcut it made
REM
REM  The venv's Python runs it once .venv exists; before that, the py launcher or
REM  python on PATH (3.11 or newer). Flat gotos, not nested blocks: an `exit /b`
REM  inside a parenthesized block can lose its code under cmd.exe. ASCII only.
REM ============================================================================

setlocal
REM  This file lives in scripts\; everything below runs from the repo root.
cd /d "%~dp0.." || exit /b 1
set "VENV_PY=%CD%\.venv\Scripts\python.exe"

if exist "%VENV_PY%" goto :run_venv
where py >nul 2>nul && goto :run_py
where python >nul 2>nul && goto :run_python
echo ERROR: no Python found. Install Python 3.11 or newer from python.org, then run scripts\install.bat again.
exit /b 1

:run_venv
"%VENV_PY%" -m app.install %*
exit /b %ERRORLEVEL%

:run_py
py -3 -m app.install %*
exit /b %ERRORLEVEL%

:run_python
python -m app.install %*
exit /b %ERRORLEVEL%
