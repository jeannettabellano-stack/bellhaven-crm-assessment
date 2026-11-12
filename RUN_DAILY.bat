@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run START_HERE.bat once first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" run_daily.py
pause
