@echo off
REM Windows launcher — double-click or run in CMD
cd /d "%~dp0"
python -m pip install -r requirements.txt
python app.py
pause
