@echo off
REM Push the CURRENT source + TLS material to the box, rebuild the venv, (re)start the server.
REM Run this after every code change - the box does not update itself.
cd /d "%~dp0.."
python mt5_ec2.py ship
echo.
pause
