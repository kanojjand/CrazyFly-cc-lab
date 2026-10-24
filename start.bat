@echo off
rem Crazyflie Pilot launcher. Arguments are passed through, e.g.: start.bat --sim
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [setup] Creating virtual environment and installing packages...
    python -m venv .venv
    if errorlevel 1 (
        echo Python not found. Install Python 3.10+ from python.org and tick "Add python.exe to PATH".
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo pip install failed, see the messages above.
        pause
        exit /b 1
    )
)
".venv\Scripts\python.exe" main.py %*
if errorlevel 1 pause
