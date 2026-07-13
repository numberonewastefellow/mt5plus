@echo off
REM Give the box a STABLE public IP and open the mTLS port (8443) - and ONLY that port.
REM The server certificate is bound to this address, so it must never change again.
REM The box must be RUNNING (an Elastic IP can only attach to a running instance).
cd /d "%~dp0.."
python mt5_ec2.py eip
echo.
pause
