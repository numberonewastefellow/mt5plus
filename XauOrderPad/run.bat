@echo off
REM Launch the XAU Order Pad: starts the local server and opens the browser.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [setup] creating virtual environment...
  py -V:3.12 -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

start "" http://127.0.0.1:8765
".venv\Scripts\python.exe" server.py
pause
