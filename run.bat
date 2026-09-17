@echo off
REM Windows launcher — project-local venv, no system pip pollution.
cd /d "%~dp0"
.venv\Scripts\python -c "import sys" >nul 2>&1
if errorlevel 1 (
  rmdir /s /q .venv
  python -m venv .venv
)
if not exist .venv\Scripts\python.exe (
  python -m venv .venv
)
REM Install deps only when missing (fast restarts); pass --upgrade to refresh.
.venv\Scripts\python -c "import yt_dlp" >nul 2>&1
if errorlevel 1 (
  .venv\Scripts\python -m pip install -r requirements.txt
)
if "%1"=="--upgrade" (
  .venv\Scripts\python -m pip install -U -r requirements.txt
)
.venv\Scripts\python app.py
pause
