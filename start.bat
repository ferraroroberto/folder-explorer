@echo off
chcp 65001 >nul
REM ============================================================================
REM  folder-explorer - run the web app in this console, no tray (Ctrl-C stops it)
REM  Creates .venv on first run. Remote access, port and roots come from
REM  config.json (see config.example.json).
REM ============================================================================
setlocal
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

if not exist ".venv\Scripts\python.exe" (
    echo [setup] creating virtual environment...
    python -m venv .venv || (
        echo [error] Python 3.11+ is required and must be on PATH.
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
    ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt || (
        echo [error] dependency install failed - check your network/proxy.
        exit /b 1
    )
)

".venv\Scripts\python.exe" -m app.webapp %*
