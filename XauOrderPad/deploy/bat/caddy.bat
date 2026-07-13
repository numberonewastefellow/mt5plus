@echo off
REM Install / refresh the mTLS front door on port 8443 and start it.
REM It self-tests: a connection with NO client certificate must be rejected, or it refuses to
REM report success. Run 'ship' first - Caddy needs the certs that ship installs.
cd /d "%~dp0.."
python mt5_ec2.py caddy
echo.
pause
