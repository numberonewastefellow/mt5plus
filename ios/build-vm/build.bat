@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ===========================================================================
rem  build.bat -- build XauOrderPad.ipa in the macOS VM, drop it on Windows.
rem
rem  This is the iOS sibling of android\deploy.bat. Same SPLIT-BY-DESIGN idea:
rem    build   -> inside the VM (the Xcode toolchain lives only in macOS)
rem    install -> from WINDOWS (Sideloadly; Docker Desktop cannot pass USB through)
rem
rem  The bridge between host and VM is the 9p shared folder (.\shared <-> /shared).
rem  Because .\shared is a Windows bind-mount, the .ipa the VM writes to
rem  /shared/out appears directly on Windows -- no named-volume copy needed
rem  (that is the one thing simpler here than the Android APK handoff).
rem
rem  The ONE step that cannot be driven from Windows is running xcodebuild, which
rem  must happen inside macOS. Two ways:
rem    (a) set XAU_VM_SSH and this script SSHes in and runs it for you, or
rem    (b) run  bash /shared/build.sh  in the VM Terminal, then: build.bat fetch
rem  See README.md for enabling Remote Login (SSH) in the guest.
rem ===========================================================================

pushd "%~dp0"

set "SVC=build"
set "SCHEME=XauOrderPad"
set "SRC_APP=..\XauOrderPad"
set "SRC_PROJ=..\project.yml"
set "EXPORT_PLIST=ExportOptions-unsigned.plist"
set "SHARED=%~dp0shared"
set "SHARED_SRC=%SHARED%\src"
set "IPA_SHARED=%SHARED%\out\%SCHEME%.ipa"
rem Canonical host location -- same folder the Android APK lands in.
set "OUT_W=E:\temp\mt5_data\%SCHEME%.ipa"

set "CMD=%~1"
if not defined CMD set "CMD=all"

if /I "%CMD%"=="all"    goto :cmd_all
if /I "%CMD%"=="up"     goto :cmd_up
if /I "%CMD%"=="sync"   goto :cmd_sync
if /I "%CMD%"=="build"  goto :cmd_build
if /I "%CMD%"=="fetch"  goto :cmd_fetch
if /I "%CMD%"=="open"   goto :cmd_open
if /I "%CMD%"=="status" goto :cmd_status
if /I "%CMD%"=="down"   goto :cmd_down
goto :usage


rem ---------------------------------------------------------------- all -----
rem The hot loop: ensure VM up, sync source, build in the VM, fetch the IPA.
:cmd_all
call :ensure_container || goto :fail
call :do_sync || goto :fail
call :trigger
set "TRC=!errorlevel!"
if "!TRC!"=="2" goto :done
if not "!TRC!"=="0" goto :fail
call :do_fetch || goto :fail
echo.
echo [build] OK
goto :done


rem ----------------------------------------------------------------- up -----
:cmd_up
call :ensure_container || goto :fail
echo [build] VM container is up. Open the viewer:  build.bat open
goto :done


rem --------------------------------------------------------------- sync -----
:cmd_sync
call :do_sync || goto :fail
echo [build] source synced to shared\src (visible in the VM at /shared/src).
goto :done


rem -------------------------------------------------------------- build -----
:cmd_build
call :ensure_container || goto :fail
call :do_sync || goto :fail
call :trigger
set "TRC=!errorlevel!"
if "!TRC!"=="2" goto :done
if not "!TRC!"=="0" goto :fail
call :do_fetch || goto :fail
goto :done


rem -------------------------------------------------------------- fetch -----
rem Copy the IPA the VM produced into E:\temp\mt5_data (next to the APK).
:cmd_fetch
call :do_fetch || goto :fail
goto :done


rem --------------------------------------------------------------- open -----
:cmd_open
start "" http://localhost:8006
echo [build] opened the VM web viewer (http://localhost:8006).
goto :done


rem ------------------------------------------------------------- status -----
:cmd_status
echo === container ===
docker compose ps
echo.
echo === IPA on Windows ===
if exist "%OUT_W%" (call :show_ipa) else (echo   (none at %OUT_W%^))
echo.
echo === IPA in shared\out ===
if exist "%IPA_SHARED%" (echo   %IPA_SHARED%) else (echo   (none -- VM build has not produced one yet^))
goto :done


rem --------------------------------------------------------------- down -----
:cmd_down
echo [build] stopping the VM container (disk + Xcode persist in .\storage)...
docker compose stop
goto :done


rem ======================================================= helpers ==========

rem Start the container only if not already running -- recreating it reboots the
rem whole macOS VM, which is slow. Mirrors android deploy.bat's :ensure_container.
:ensure_container
set "CID="
for /f "delims=" %%i in ('docker compose ps -q %SVC% 2^>nul') do set "CID=%%i"
if defined CID exit /b 0
echo [build] VM container not running -- starting it...
docker compose up -d
if errorlevel 1 (
    echo [build] could not start the container. Is Docker Desktop running, and is
    echo [build] /dev/kvm available to WSL2? See README.md prerequisites.
    exit /b 1
)
echo [build] started. First boot installs macOS -- drive it at http://localhost:8006
exit /b 0

rem Push the latest source into the shared folder so the VM builds current code.
:do_sync
echo [build] syncing source into shared\src ...
if not exist "%SHARED_SRC%\XauOrderPad" mkdir "%SHARED_SRC%\XauOrderPad" >nul 2>&1
robocopy "%SRC_APP%" "%SHARED_SRC%\XauOrderPad" /MIR /NFL /NDL /NJH /NJS /NP >nul
if errorlevel 8 (
    echo [build] robocopy failed syncing the app sources.
    exit /b 1
)
copy /Y "%SRC_PROJ%" "%SHARED_SRC%\project.yml" >nul
copy /Y "%EXPORT_PLIST%" "%SHARED%\%EXPORT_PLIST%" >nul
exit /b 0

rem Trigger the actual xcodebuild INSIDE the VM. SSH if configured, else instruct.
:trigger
if defined XAU_VM_SSH (
    echo [build] building in the VM over SSH ^(XAU_VM_SSH=%XAU_VM_SSH%^)...
    ssh %XAU_VM_SSH% "bash /shared/build.sh"
    if errorlevel 1 (
        echo [build] the VM build failed -- see the SSH output above.
        exit /b 1
    )
    exit /b 0
)
echo.
echo [build] SSH is not configured, so run the build inside the VM:
echo [build]    1^) build.bat open        ^(opens http://localhost:8006^)
echo [build]    2^) in the VM Terminal:   bash /shared/build.sh
echo [build]    3^) back here:            build.bat fetch
echo.
echo [build] ^(To automate: enable Remote Login in macOS and set XAU_VM_SSH, e.g.
echo [build]   set XAU_VM_SSH=-p 22 you@VM_IP    -- see README.md.^)
exit /b 2

rem Copy the VM-produced IPA to the canonical Windows location (next to the APK).
:do_fetch
if not exist "%IPA_SHARED%" (
    echo [build] No IPA at %IPA_SHARED%
    echo [build] The VM build has not produced one. Run  bash /shared/build.sh  in the VM,
    echo [build] then:  build.bat fetch
    exit /b 1
)
if not exist "E:\temp\mt5_data" mkdir "E:\temp\mt5_data" >nul 2>&1
copy /Y "%IPA_SHARED%" "%OUT_W%" >nul
if errorlevel 1 (
    echo [build] failed copying the IPA to %OUT_W%
    exit /b 1
)
call :show_ipa
echo.
echo [build] Install it from Windows with Sideloadly (free Apple ID, USB):
echo [build]   drag %OUT_W% into Sideloadly, enter your Apple ID, install.
exit /b 0

:show_ipa
if not exist "%OUT_W%" exit /b 0
powershell -NoProfile -Command "$f = Get-Item -LiteralPath '%OUT_W%'; '[build] IPA: {0}' -f $f.FullName; '[build]      {0:N1} MB   built {1}' -f ($f.Length/1MB), $f.LastWriteTime"
exit /b 0

:usage
echo.
echo   build.bat [command]
echo.
echo     (none) ^| all   up + sync + build-in-VM + fetch          ^<- the hot loop
echo     up            start the macOS VM container
echo     sync          copy source into the shared folder (.\shared\src)
echo     build         sync + build in the VM + fetch the IPA
echo     fetch         copy the VM-built IPA to %OUT_W%
echo     open          open the VM web viewer (http://localhost:8006)
echo     status        container + IPA locations
echo     down          stop the VM container (disk + Xcode persist)
echo.
echo   The xcodebuild step runs INSIDE macOS. Set XAU_VM_SSH to have this script
echo   run it for you; otherwise run  bash /shared/build.sh  in the VM, then fetch.
echo.
echo   IPA (Windows): %OUT_W%
echo.
goto :fail

:fail
popd
endlocal
exit /b 1

:done
popd
endlocal
exit /b 0
