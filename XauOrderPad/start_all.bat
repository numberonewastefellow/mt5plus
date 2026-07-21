@echo off
REM Start EVERY account in instances.json, each in its own window, plus the monitor.
REM
REM Each instance gets: its own port, its own terminal64.exe (/portable), its own API
REM token, its own log folder and its own state. The account lock then guarantees no
REM two of them can drive the same account -- a second attempt is refused with the
REM name of the server already holding it.
REM
REM Terminals are started if not already running, and are NOT stopped by stop_all.bat
REM (they hold your broker sessions).
REM
REM The monitor is READ-ONLY and loopback-only: http://127.0.0.1:8760
cd /d "%~dp0"

if not exist "instances.json" (
  echo [error] instances.json not found.
  echo         Copy instances.json.example and edit it - one entry per account,
  echo         each with its own port and its own terminal64.exe.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\activate.bat" (
  echo [setup] .venv not found - creating it and installing dependencies...
  py -V:3.12 -m venv .venv
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
) else (
  call ".venv\Scripts\activate.bat"
)

python launch.py --all
pause
