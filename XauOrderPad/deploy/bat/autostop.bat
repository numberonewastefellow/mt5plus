@echo off
cd /d "%~dp0.."
set /p MINS="Auto-stop this box after how many minutes? (e.g. 60): "
python mt5_ec2.py autostop %MINS%
echo.
pause
