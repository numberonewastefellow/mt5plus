@echo off
REM Double-clickable launcher for TLS (encrypted LAN) mode.
REM
REM This is the mode that lets the phone add/log in a REAL account: uvicorn stays on 127.0.0.1
REM (loopback) and a local Caddy mTLS front door runs on 8443 using the LAN cert
REM (deploy\certs-lan\server.crt). The phone then connects to  https://192.168.0.116:8443.
REM
REM Equivalent to running:  start_server.bat tls
cd /d "%~dp0"
call "%~dp0start_server.bat" tls
