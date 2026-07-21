@echo off
REM Stop every instance listed in instances.json, plus the monitor.
REM
REM BY PORT, never by image name. `taskkill /IM python.exe` on this machine would
REM also kill the single-account server on 8765 - which is not in instances.json and
REM may be supervising open positions on an account you did not ask to stop.
REM
REM The MT5 terminals are deliberately left running: they hold the broker sessions,
REM and closing them logs the accounts out.
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo [error] .venv not found - nothing to stop.
  pause
  exit /b 1
)
call ".venv\Scripts\activate.bat"

python launch.py --stop
pause
