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

REM --- Read the API token from .token.local (gitignored). Both modes want it. -----------------
REM `for /f` (not `set /p ... <file`): the redirect form does not evaluate reliably inside a
REM parenthesized if-block and silently leaves the token empty.
REM
REM Read BEFORE the port-kill below, because the bypass guard needs it: whether we would bind
REM 0.0.0.0 depends on whether a token exists, and that decision has to be made while the
REM current server is still ALIVE.
set "XAUORDERPAD_TOKEN="
if exist ".token.local" for /f "usebackq delims=" %%T in (".token.local") do set "XAUORDERPAD_TOKEN=%%T"

REM --- mTLS bypass guard: refuse BEFORE touching anything ------------------------------------
REM Deliberately ahead of the port-kill. Refusing after it would leave the operator with a
REM stopped server AND no new one -- a guard that takes the system down is worse than the hole
REM it closes. Nothing has been killed at this point, so the refusal is a genuine no-op.
REM
REM Only in plain mode (tls mode frees %TLS_PORT% itself and re-starts Caddy), and only when a
REM token exists -- without one we stay on 127.0.0.1, which is not reachable off this machine
REM and therefore not a bypass. See :bypass_refused for the full reasoning.
set "TLS_LISTENER="
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /R /C:":%TLS_PORT% .*LISTENING"') do set "TLS_LISTENER=%%P"
if /I not "%MODE%"=="tls" if defined XAUORDERPAD_TOKEN if defined TLS_LISTENER goto :bypass_refused

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

if /I "%MODE%"=="tls" goto run_tls

REM ============================ PLAIN mode (plain HTTP over the LAN) ============================
REM WHY 0.0.0.0 + token together: a bare `python server.py` binds 127.0.0.1 (loopback), which a
REM phone cannot reach; and the server REFUSES a network bind without a token (fail-closed).
REM
REM (The mTLS bypass guard already ran near the top, BEFORE the port-kill -- see there.)
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

:bypass_refused
REM Reached only when a token exists (so we WOULD bind 0.0.0.0) AND something already holds the
REM TLS port. Fail closed, exactly like the server's own refusal to bind the network with no token.
echo.
echo [REFUSING TO START] Port %TLS_PORT% is already in use (PID %TLS_LISTENER%) -- almost certainly
echo                     the Caddy mTLS front door.
echo.
echo   Starting PLAIN mode now would bind uvicorn to 0.0.0.0:%PORT% in CLEARTEXT while that
echo   encrypted door is still open: ONE trading API behind TWO doors, one of them unencrypted
echo   and guarded by nothing but a bearer token. The phone would still show https and still
echo   work, so nothing would look wrong.
echo.
echo   Pick the one you actually want:
echo     * keep TLS (recommended):  start_server.bat tls
echo     * go plain:                stop Caddy first, then re-run this --
echo                                taskkill /F /PID %TLS_LISTENER%
echo.
pause
exit /b 1

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
