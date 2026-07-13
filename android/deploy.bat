@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ===========================================================================
rem  deploy.bat -- build XauOrderPad in the container, install it from Windows.
rem
rem  SPLIT BY DESIGN:
rem    build   -> in the Docker container (the Android SDK lives only there)
rem    install -> from WINDOWS adb (the container cannot do USB, and wireless
rem               pairing from inside the container is an extra hoop for no gain)
rem
rem  The bridge between the two halves is the APK copy. app/build is a NAMED
rem  VOLUME -- that is why builds are ~3 s, and also why the APK is invisible
rem  from Windows. It has to be copied to /out (-> E:\temp\mt5_data) before
rem  Windows adb can see it. That copy is the price of this split; it costs a
rem  few seconds and is unavoidable.
rem
rem  USB needs no pairing at all. Wireless needs `deploy.bat pair` once.
rem ===========================================================================

pushd "%~dp0"

set "SVC=build"
set "PKG=com.xauorderpad"
set "LAUNCH=%PKG%/.MainActivity"
set "APK_C=/workspace/app/build/outputs/apk/debug/app-debug.apk"
set "OUT_C=/out/app-debug.apk"
set "OUT_W=E:\temp\mt5_data\app-debug.apk"
rem Release variant paths (signed; see :cmd_release). Different APK, different signing key.
set "APK_C_REL=/workspace/app/build/outputs/apk/release/app-release.apk"
set "OUT_C_REL=/out/app-release.apk"
set "OUT_W_REL=E:\temp\mt5_data\app-release.apk"
set "PHONE_FILE=%~dp0.deploy-phone"

rem --- locate Windows adb -----------------------------------------------------
set "ADB="
for /f "delims=" %%a in ('where adb 2^>nul') do if not defined ADB set "ADB=%%a"
if not defined ADB (
    set "TRY=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools\adb.exe"
    if exist "!TRY!" set "ADB=!TRY!"
)
if not defined ADB (
    echo [deploy] Windows adb not found.
    echo [deploy] Install it:  winget install Google.PlatformTools
    goto :fail
)

rem Phone address for wireless: env var wins, else the file written by `connect`.
set "PHONE=%XAU_PHONE%"
if not defined PHONE if exist "%PHONE_FILE%" set /p PHONE=<"%PHONE_FILE%"

set "CMD=%~1"
if not defined CMD set "CMD=all"

if /I "%CMD%"=="all"       goto :cmd_all
if /I "%CMD%"=="build"     goto :cmd_build
if /I "%CMD%"=="release"   goto :cmd_release
if /I "%CMD%"=="install"   goto :cmd_install
if /I "%CMD%"=="pair"      goto :cmd_pair
if /I "%CMD%"=="connect"   goto :cmd_connect
if /I "%CMD%"=="status"    goto :cmd_status
if /I "%CMD%"=="logs"      goto :cmd_logs
if /I "%CMD%"=="uninstall" goto :cmd_uninstall
goto :usage


rem ---------------------------------------------------------------- all -----
rem The hot loop: build, copy out, install, launch.
:cmd_all
call :ensure_container || goto :fail
call :ensure_device    || goto :fail

echo [deploy] building...
docker compose exec -T %SVC% sh ./gradlew :app:assembleDebug
if errorlevel 1 (
    echo.
    echo [deploy] BUILD FAILED -- nothing was deployed.
    echo [deploy] The phone is still running the PREVIOUS build.
    goto :fail
)

call :copy_out || goto :fail
call :do_install || goto :fail
call :launch
call :show_apk
echo.
echo [deploy] OK
goto :done


rem -------------------------------------------------------------- build -----
rem Compile and drop the APK where Windows can see it. No install.
:cmd_build
call :ensure_container || goto :fail

echo [deploy] building...
docker compose exec -T %SVC% sh ./gradlew :app:assembleDebug
if errorlevel 1 (
    echo.
    echo [deploy] BUILD FAILED -- no APK produced.
    goto :fail
)

call :copy_out || goto :fail
call :show_apk
goto :done


rem ------------------------------------------------------------ release -----
rem Build the SIGNED release APK. Two forms:
rem     deploy.bat release          -> CLEAN: no URL/token/cert baked in. The shippable build;
rem                                    useless if extracted (nothing to extract).
rem     deploy.bat release demo     -> embeds the dev URL + token + demo cert (-Pxau.embedSecrets),
rem                                    for handing a ready-to-run build to a tester. As extractable
rem                                    as debug, but signed with the real key and NOT debuggable.
rem
rem Signing comes from android/.env (keystore passwords) via docker-compose. If the container was
rem started BEFORE android/.env existed, it does not have those vars and the build fails the signing
rem gate -- run `docker compose up -d` once to recreate it, then retry.
:cmd_release
call :ensure_container || goto :fail

set "EMBED="
if /I "%~2"=="demo" set "EMBED=-Pxau.embedSecrets=true"
if defined EMBED (
    echo [deploy] RELEASE build WITH embedded secrets ^(demo -- URL/token/cert baked in^)
) else (
    echo [deploy] RELEASE build ^(clean, secret-free -- the shippable artifact^)
)

docker compose exec -T %SVC% sh ./gradlew :app:assembleRelease %EMBED%
if errorlevel 1 (
    echo.
    echo [deploy] RELEASE BUILD FAILED -- no APK produced.
    echo [deploy] If it complained about signing, the container may predate android/.env.
    echo [deploy] Fix:  docker compose up -d   ^(recreates it so it reads android/.env^)
    goto :fail
)

call :copy_out_release || goto :fail
call :show_apk_rel
echo.
echo [deploy] Release APK: %OUT_W_REL%
echo [deploy] Install is NOT automatic -- a release cannot update an installed DEBUG build (different
echo [deploy] key). To switch:  deploy.bat uninstall  then  adb install -r "%OUT_W_REL%"
goto :done


rem ------------------------------------------------------------ install -----
rem Push whatever APK already sits in E:\temp\mt5_data. Does NOT build --
rem which is the whole risk, so the age is printed loudly.
:cmd_install
if not exist "%OUT_W%" (
    echo [deploy] No APK at %OUT_W%
    echo [deploy] Run `deploy.bat build` first.
    goto :fail
)
call :ensure_device || goto :fail
call :show_apk
call :warn_if_stale
call :do_install || goto :fail
call :launch
echo.
echo [deploy] OK
goto :done


rem --------------------------------------------------------------- pair -----
rem Wireless only. USB never needs this.
:cmd_pair
if "%~3"=="" (
    echo Usage: deploy.bat pair ^<ip:pairing-port^> ^<6-digit-code^>
    echo.
    echo   On the phone: Developer options ^> Wireless debugging
    echo                 ^> "Pair device with pairing code"
    echo.
    echo   That dialog shows an IP:PORT *and* a code. Use THAT port -- it is a
    echo   DIFFERENT port from the one on the main Wireless debugging screen.
    echo   The dialog also expires after about a minute.
    goto :fail
)
echo [deploy] pairing with %~2 ...
"%ADB%" pair %~2 %~3
if errorlevel 1 goto :fail
echo.
echo [deploy] Paired. Now:  deploy.bat connect ^<ip^>:^<connect-port^>
echo [deploy] (the port from the MAIN wireless debugging screen)
goto :done


rem ------------------------------------------------------------ connect -----
rem Wireless only. USB never needs this.
:cmd_connect
if "%~2"=="" (
    echo Usage: deploy.bat connect ^<ip:port^>
    echo.
    echo   Use the IP:PORT from the MAIN Wireless debugging screen, NOT the one
    echo   from the pairing dialog. This port CHANGES every time wireless
    echo   debugging is toggled off and on.
    goto :fail
)
set "PHONE=%~2"
"%ADB%" connect %PHONE%
call :device_ready
if errorlevel 1 (
    echo.
    echo [deploy] Connected, but no usable device appeared.
    call :device_hint
    goto :fail
)
> "%PHONE_FILE%" echo %PHONE%
echo [deploy] connected to %PHONE%  (remembered in .deploy-phone)
goto :done


rem ------------------------------------------------------------- status -----
:cmd_status
echo === container ===
docker compose ps
echo.
echo === adb (Windows) ===
echo   %ADB%
"%ADB%" devices -l
echo.
echo === remembered wireless address ===
if defined PHONE (echo   %PHONE%) else (echo   (none -- USB needs none^))
echo.
echo === installed on phone ===
call :device_ready
if not errorlevel 1 (
    "%ADB%" shell dumpsys package %PKG% ^| findstr /R "versionCode lastUpdateTime"
) else (
    echo   (no device^)
)
echo.
echo === APK on Windows ===
if exist "%OUT_W%" (call :show_apk) else (echo   (none at %OUT_W%^))
goto :done


rem --------------------------------------------------------------- logs -----
:cmd_logs
call :ensure_device || goto :fail
set "PID="
for /f "delims=" %%p in ('"%ADB%" shell pidof -s %PKG% 2^>nul') do set "PID=%%p"
if defined PID (
    echo [deploy] logcat for pid %PID%  (Ctrl+C to stop)
    "%ADB%" logcat -v time --pid=%PID%
) else (
    echo [deploy] app is not running -- showing crashes only. Launch it, then re-run.
    "%ADB%" logcat -v time AndroidRuntime:E *:S
)
goto :done


rem ---------------------------------------------------------- uninstall -----
:cmd_uninstall
echo.
echo   This REMOVES the app and WIPES its stored data -- including the saved
echo   server URL and API token. You will have to re-enter them.
echo.
choice /C YN /N /M "   Uninstall %PKG%? [Y/N] "
if errorlevel 2 goto :done
call :ensure_device || goto :fail
"%ADB%" uninstall %PKG%
goto :done


rem ======================================================= helpers ==========

rem Start the container only if it is not already running. `up -d` against a
rem running stack can recreate it, and recreating kills the warm Gradle daemon --
rem which is exactly what turns a 3 s rebuild into a 2 min cold start.
:ensure_container
set "CID="
for /f "delims=" %%i in ('docker compose ps -q %SVC% 2^>nul') do set "CID=%%i"
if defined CID exit /b 0
echo [deploy] container not running -- starting it...
docker compose up -d
if errorlevel 1 (
    echo [deploy] could not start the container. Is Docker Desktop running?
    exit /b 1
)
exit /b 0

rem True only when adb lists a device in state `device`. Deliberately does NOT
rem accept `offline` or `unauthorized`: installing against either fails in a
rem confusing way, and it is better to say so up front.
:device_ready
"%ADB%" devices 2>nul | findstr /R /C:"device$" >nul
exit /b %errorlevel%

:ensure_device
call :device_ready
if not errorlevel 1 exit /b 0

rem Wireless: try the remembered address before giving up.
if defined PHONE (
    echo [deploy] no device -- trying %PHONE% ...
    "%ADB%" connect %PHONE% >nul 2>&1
    call :device_ready
    if not errorlevel 1 (
        echo [deploy] connected to %PHONE%
        exit /b 0
    )
)
call :device_hint
exit /b 1

:device_hint
echo.
echo [deploy] No usable device.
echo.
echo [deploy] USB (simplest -- no pairing):
echo [deploy]   Plug the phone in, enable USB debugging, and tap ALLOW on the
echo [deploy]   phone's prompt. Then re-run. Nothing else is needed.
echo.
echo [deploy] Wireless:
echo [deploy]   1. Wireless debugging ON (it turns itself off).
echo [deploy]   2. Pair once:  deploy.bat pair ^<ip^>:^<pairing-port^> ^<code^>
echo [deploy]      (from "Pair device with pairing code" -- its own port + code)
echo [deploy]   3. Connect:    deploy.bat connect ^<ip^>:^<connect-port^>
echo [deploy]      (the port on the MAIN screen -- it CHANGES on every toggle)
echo.
echo [deploy]   If `deploy.bat status` shows `unauthorized`, pair again.
exit /b 1

rem The APK is in a named volume, so this container-internal copy is the ONLY
rem way it becomes visible to Windows adb. It crosses the gRPC-FUSE boundary,
rem so it costs a few seconds -- that is the cost of installing from Windows.
:copy_out
echo [deploy] copying APK to %OUT_W% ...
docker compose exec -T %SVC% cp %APK_C% %OUT_C%
if errorlevel 1 (
    echo [deploy] copy failed. Did the build actually produce %APK_C%?
    exit /b 1
)
if not exist "%OUT_W%" (
    echo [deploy] copy reported success but %OUT_W% is not there.
    echo [deploy] Is E:/temp/mt5_data still mounted at /out in docker-compose.yml?
    exit /b 1
)
exit /b 0

rem Release counterpart of :copy_out (named-volume APK -> Windows-visible /out).
:copy_out_release
echo [deploy] copying release APK to %OUT_W_REL% ...
docker compose exec -T %SVC% cp %APK_C_REL% %OUT_C_REL%
if errorlevel 1 (
    echo [deploy] copy failed. Did assembleRelease actually produce %APK_C_REL%?
    exit /b 1
)
if not exist "%OUT_W_REL%" (
    echo [deploy] copy reported success but %OUT_W_REL% is not there.
    exit /b 1
)
exit /b 0

:show_apk_rel
if not exist "%OUT_W_REL%" exit /b 0
powershell -NoProfile -Command "$f = Get-Item -LiteralPath '%OUT_W_REL%'; '[deploy] APK: {0}' -f $f.FullName; '[deploy]      {0:N1} MB   built {1}' -f ($f.Length/1MB), $f.LastWriteTime"
exit /b 0

:do_install
echo [deploy] installing...
rem -r reinstalls over the existing app and KEEPS its data, so the saved server
rem URL and API token survive a redeploy.
"%ADB%" install -r "%OUT_W%"
if errorlevel 1 (
    echo.
    echo [deploy] INSTALL FAILED.
    echo [deploy] If it said INSTALL_FAILED_UPDATE_INCOMPATIBLE, the installed app
    echo [deploy] was signed with a different key. Fixing that means uninstalling,
    echo [deploy] which WIPES the saved server token -- so do it deliberately:
    echo [deploy]     deploy.bat uninstall
    exit /b 1
)
exit /b 0

:launch
echo [deploy] launching...
"%ADB%" shell am start -n %LAUNCH% >nul
exit /b 0

:show_apk
if not exist "%OUT_W%" exit /b 0
powershell -NoProfile -Command "$f = Get-Item -LiteralPath '%OUT_W%'; '[deploy] APK: {0}' -f $f.FullName; '[deploy]      {0:N1} MB   built {1}' -f ($f.Length/1MB), $f.LastWriteTime"
exit /b 0

rem `install` does not build. If the APK is old, the user is very likely about to
rem push code that is not the code they just edited.
:warn_if_stale
powershell -NoProfile -Command "$m = [int]((Get-Date) - (Get-Item -LiteralPath '%OUT_W%').LastWriteTime).TotalMinutes; if ($m -gt 10) { ''; '  *** WARNING: that APK is ' + $m + ' minutes old. ***'; '  *** install does NOT build -- you may be deploying STALE code. ***'; '  *** Use deploy.bat (all) to build and install together. ***'; '' }"
exit /b 0

:usage
echo.
echo   deploy.bat [command]
echo.
echo     (none) ^| all      build + install + launch          ^<- the hot loop
echo     build             build + copy APK to Windows, no install
echo     release           build the SIGNED, secret-free release APK
echo     release demo      signed release WITH url/token/cert baked in (for a tester)
echo     install           install the existing APK (does NOT build)
echo.
echo     pair ^<ip:port^> ^<code^>   wireless only, once
echo     connect ^<ip:port^>       wireless only
echo     status                  container / adb / installed version
echo     logs                    logcat, filtered to the app
echo     uninstall               removes the app AND its saved token
echo.
echo   USB needs neither pair nor connect -- just plug in and tap ALLOW.
echo.
echo   APK (Windows): %OUT_W%
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
