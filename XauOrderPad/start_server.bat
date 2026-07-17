@echo off
REM Activate the virtual environment and run the server.
REM
REM   start_server.bat            same as "plain"
REM   start_server.bat plain      PLAIN HTTP on the LAN (uvicorn on 0.0.0.0:8765 + token).
REM                               Simple, but the token crosses the LAN in CLEARTEXT.
REM   start_server.bat tls        uvicorn on 127.0.0.1 (loopback) + a local Caddy mTLS front door
REM                               on 8443 using the LAN cert. Encrypted; lets the phone add a REAL
REM                               account. Reuses the SAME CA/client cert as the EC2 box.
REM
REM WHICH CERTIFICATE, AND WHEN -- so a wrong deployment is impossible:
REM   * tls mode here uses ONLY  deploy\certs-lan\server.crt  (SAN = the LAN IP 192.168.0.116).
REM   * the EC2 box uses         deploy\certs\server.crt      (SAN = the Elastic IP) -- NOT touched
REM     here, and `mt5_ec2.py ship` copies only deploy\certs\, so the LAN cert can never be shipped.
cd /d "%~dp0"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=plain"
if /I "%MODE%"=="plain" goto mode_ok
if /I "%MODE%"=="tls"   goto mode_ok
echo [error] unknown mode "%MODE%". Use:  start_server.bat [plain^|tls]
pause
exit /b 1
:mode_ok

set PORT=8765
set TLS_PORT=8443

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

REM --- Read the API token from .token.local (gitignored). Both modes want it. -----------------
REM `for /f` (not `set /p ... <file`): the redirect form does not evaluate reliably inside a
REM parenthesized if-block and silently leaves the token empty.
set "XAUORDERPAD_TOKEN="
if exist ".token.local" for /f "usebackq delims=" %%T in (".token.local") do set "XAUORDERPAD_TOKEN=%%T"

if /I "%MODE%"=="tls" goto run_tls

REM ============================ PLAIN mode (plain HTTP over the LAN) ============================
REM WHY 0.0.0.0 + token together: a bare `python server.py` binds 127.0.0.1 (loopback), which a
REM phone cannot reach; and the server REFUSES a network bind without a token (fail-closed).
if defined XAUORDERPAD_TOKEN (
  set "XAUORDERPAD_HOST=0.0.0.0"
  echo [net] PLAIN HTTP -- LAN-reachable on port %PORT% ^(token from .token.local^).
  echo [net] NOTE: the token crosses the LAN in CLEARTEXT. For an ENCRYPTED LAN link that also lets
  echo [net]       the phone add a REAL account, use:  start_server.bat tls
) else (
  echo [net] no token in .token.local -- staying on 127.0.0.1 loopback ^(the phone will NOT connect^).
  echo [net] Put a token in XauOrderPad\.token.local to expose it to the LAN.
)
echo [run] starting XAU Order Pad (plain)...
python server.py
pause
exit /b 0

:run_tls
REM ============================ TLS mode (loopback uvicorn + Caddy mTLS) ========================
if not defined XAUORDERPAD_TOKEN (
  echo [error] TLS mode needs a token in XauOrderPad\.token.local -- it is the inner factor behind mTLS.
  pause
  exit /b 1
)
REM The LOCAL Caddy uses the LAN cert, NEVER the EC2 cert. Refuse to start without it.
if not exist "deploy\certs-lan\server.crt" (
  echo [error] LAN cert missing: deploy\certs-lan\server.crt
  echo [error] Mint it once ^(same CA as EC2; does NOT touch the box^):
  echo           cd deploy ^&^& python make_certs.py --ip 192.168.0.116 --server-only --out certs-lan
  pause
  exit /b 1
)

REM uvicorn stays on LOOPBACK: Caddy on 8443 is the only network door, so if it dies the trading API
REM is unreachable rather than reachable-in-cleartext (fail-closed, same as the EC2 box). Do NOT set
REM 0.0.0.0 here -- that would put plain HTTP on the LAN and BYPASS the TLS front door.
set "XAUORDERPAD_HOST=127.0.0.1"

echo [port] freeing TLS port %TLS_PORT% for a fresh Caddy...
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%TLS_PORT% .*LISTENING"') do (
  echo [port] killing PID %%P listening on port %TLS_PORT%
  taskkill /F /PID %%P >nul 2>&1
)

echo [run] starting uvicorn on 127.0.0.1:%PORT% (loopback) in a separate window...
start "XauOrderPad uvicorn (loopback:%PORT%)" cmd /k python server.py

echo [run] starting the LAN mTLS front door (Caddy) on %TLS_PORT% using deploy\certs-lan\server.crt ...
echo [run]   phone -^> https://192.168.0.116:%TLS_PORT%
powershell -ExecutionPolicy Bypass -File "deploy\caddy_local.ps1"
pause
exit /b 0
