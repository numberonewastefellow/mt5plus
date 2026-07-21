@echo off
REM Start ONE account's server in this window.
REM
REM   start_instance.bat a1
REM
REM The instance is defined by instances.json (port + its own terminal64.exe). This
REM script only activates the venv; launch.py does the real work, because batch
REM cannot parse JSON and hand-copied env blocks are how two instances end up
REM sharing a terminal.
REM
REM Single-account users want start_server.bat, not this. Nothing here changes that
REM path: with no XAUORDERPAD_INSTANCE set the server behaves exactly as it always has.
cd /d "%~dp0"

if "%~1"=="" (
  echo [error] which instance? e.g.  start_instance.bat a1
  echo.
  echo Configured:
  if exist ".venv\Scripts\python.exe" ( .venv\Scripts\python.exe launch.py --list )
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

python launch.py %1
pause
