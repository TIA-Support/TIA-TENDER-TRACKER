@echo off
title TIA Solutions – Tender Tracker
echo.
echo  =========================================================
echo   TIA Solutions ^| ICT Tender Tracker
echo  =========================================================
echo.

cd /d "%~dp0"

:: ── Check Python ───────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python 3.10+ is required but was not found.
    echo          Download it from https://www.python.org/downloads/
    echo.
    pause & exit /b 1
)

:: ── Virtual environment ────────────────────────────────────
if not exist ".venv\Scripts\activate.bat" (
    echo  [Setup] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 ( echo  [ERROR] Failed to create venv. & pause & exit /b 1 )
)

call .venv\Scripts\activate.bat

:: ── Install / update Python dependencies ──────────────────
echo  [Setup] Checking Python dependencies...
pip install -q -r requirements.txt
if errorlevel 1 ( echo  [ERROR] pip install failed. & pause & exit /b 1 )

:: ── Install Playwright browser (skipped if already present) 
echo  [Setup] Checking Playwright browser...
playwright install chromium >nul 2>&1

:: ── Discover local network IP ──────────────────────────────
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /i "IPv4"') do (
    set "LOCAL_IP=%%a"
    goto :ip_done
)
:ip_done
set "LOCAL_IP=%LOCAL_IP: =%"

:: ── Launch server ──────────────────────────────────────────
echo.
echo  =========================================================
echo   Server starting...
echo.
echo   Local:    http://localhost:5000
if defined LOCAL_IP (
    echo   Network:  http://%LOCAL_IP%:5000
)
echo.
echo   Press Ctrl+C to stop the server.
echo  =========================================================
echo.

:: Open browser after a short delay (background job)
start /b cmd /c "timeout /t 2 >nul && start http://localhost:5000"

python app.py

echo.
echo  Server stopped.
pause

