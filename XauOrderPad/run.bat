@echo off
REM Launch the XAU Order Pad: starts the local server and opens the browser.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [setup] creating virtual environment...
  py -V:3.12 -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

REM The server itself opens the UI in a Chrome/Edge app-mode window on startup
REM (see config.LAUNCH_BROWSER), so we no longer open the default browser here.
".venv\Scripts\python.exe" server.py
pause
