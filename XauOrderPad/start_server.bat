@echo off
REM Activate the virtual environment and run the server.
cd /d "%~dp0"

set PORT=8765
echo [port] checking for existing process on port %PORT%...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  echo [port] killing PID %%P listening on port %PORT%
  taskkill /F /PID %%P >nul 2>&1
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

echo [run] starting XAU Order Pad on http://127.0.0.1:8765
python server.py

pause
