@echo off
title Jobee - Local Job Engine
echo ====================================================
echo Starting Jobee: 100% Local Job Discovery & Tracker
echo ====================================================
cd /d "%~dp0"

REM Activate virtualenv if present, otherwise use system python
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Open browser after 2 seconds in background
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://localhost:8000"

python app.py
pause
