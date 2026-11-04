@echo off
REM Windows launcher — project-local venv, no system pip pollution.
cd /d "%~dp0"
.venv\Scripts\python -c "import sys" >nul 2>&1
if errorlevel 1 (
  if exist .venv rmdir /s /q .venv
  python -m venv .venv
)
if not exist .venv\Scripts\python.exe (
  python -m venv .venv
)
REM Install deps only when missing (fast restarts). --upgrade refreshes the
REM pinned set and then moves the download engines to their latest releases:
REM a site that changed its player often needs a newer engine, not the pin.
if "%1"=="--upgrade" (
  .venv\Scripts\python -m pip install -r requirements.txt
  .venv\Scripts\python -m pip install -U yt-dlp gallery-dl
) else (
  .venv\Scripts\python -c "import yt_dlp, gallery_dl" >nul 2>&1
  if errorlevel 1 (
    .venv\Scripts\python -m pip install -r requirements.txt
  )
)
.venv\Scripts\python app.py
pause
