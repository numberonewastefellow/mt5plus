@echo off
REM ---------------------------------------------------------------------------
REM  Reset the Exness DEMO account balance to $1 (double-click me).
REM
REM  All the logic - and every safety check - is in reset_demo_balance.ps1.
REM  This wrapper only picks an interpreter, forwards arguments, and pauses so a
REM  double-click leaves the result on screen instead of flashing a window.
REM
REM  NO CREDENTIALS HERE. The session lives in
REM    %USERPROFILE%\Documents\MilkyAppData\MyCred\exness_session.txt
REM  and is refreshed with:  reset_demo_balance.bat -ImportCurl <dump file>
REM ---------------------------------------------------------------------------
setlocal

set "PSEXE=powershell"
where /q pwsh && set "PSEXE=pwsh"

"%PSEXE%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0reset_demo_balance.ps1" %*
set "RC=%ERRORLEVEL%"

echo.
REM Always hold the window open. This is an operator tool that is normally
REM double-clicked, and the exit code alone is useless if the message that
REM explains it (an expired session, a 403) has already scrolled away.
pause
exit /b %RC%
