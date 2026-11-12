@echo off
title Bellhaven CRM Assessment
cd /d "%~dp0"
echo.
echo Bellhaven CRM Assessment - first run may take a few minutes.
echo This starts a local browser app. No CRM data will be changed without approval.
echo.
if not exist ".venv\Scripts\python.exe" (
    where py >nul 2>nul
    if not errorlevel 1 (
        py -m venv .venv
    ) else (
        python -m venv .venv
    )
)
if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Python installation not found. Ask ChatGPT for setup help.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Could not install required packages. Leave this window open.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m streamlit run app.py
pause
