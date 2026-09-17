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
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python app.py
pause
