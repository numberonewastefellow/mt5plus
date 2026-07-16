<#
    Runs ON THE BOX, pushed by `mt5_ec2.py ship`. Unpacks the source bundle, installs the TLS
    material, rebuilds the venv, and registers the `xauorderpad` scheduled task.

    Nothing in this repo used to do any of this. `user_data.ps1` installs OpenSSH, Python, MT5 and
    the autostop task, then leaves an EMPTY C:\app. The app was hand-copied once, and the box has
    been quietly drifting behind the repo ever since.
#>
[CmdletBinding()]
param(
    [string]$AppDir  = "C:\app\XauOrderPad",
    [string]$CertDir = "C:\app\certs",
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "==> $m" }

# Say WHICH line failed. Without this a remote failure arrives as one context-free sentence
# ("A parameter cannot be found...") with no file, no line, and no way to tell which of forty
# statements produced it -- so you end up guessing, and guessing is how you fix the wrong thing.
trap {
    Write-Host ""
    Write-Host "FAILED at line $($_.InvocationInfo.ScriptLineNumber):"
    Write-Host "    $($_.InvocationInfo.Line.Trim())"
    Write-Host "  $($_.Exception.Message)"
    exit 1
}

$TASK = "xauorderpad"
$PY = Join-Path $AppDir ".venv\Scripts\python.exe"
$WRAPPER = "C:\app\run_server.ps1"

# --- 1. Unpack ----------------------------------------------------------------
Step "Unpacking source"
if (-not (Test-Path "C:\app\app.tar")) { throw "C:\app\app.tar is missing - did scp fail?" }

# Stop the server BEFORE overwriting its files, or Windows locks the .py files it has mapped and
# tar silently fails to replace them -- leaving a half-updated tree that starts and misbehaves.
try { Stop-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue } catch { }
Start-Sleep -Seconds 2

New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
# bsdtar ships with Windows Server 2022. -m: don't preserve mtimes from the archive.
tar -xf "C:\app\app.tar" -C $AppDir
if ($LASTEXITCODE -ne 0) { throw "tar failed with exit $LASTEXITCODE" }
Remove-Item "C:\app\app.tar" -Force -ErrorAction SilentlyContinue

if (-not (Test-Path (Join-Path $AppDir "server.py"))) { throw "server.py is not in the unpacked tree." }

# --- 2. TLS material ----------------------------------------------------------
Step "Installing TLS material"
New-Item -ItemType Directory -Force -Path $CertDir | Out-Null
foreach ($n in @("server.crt", "server.key", "ca.crt")) {
    $src = "C:\app\_cert_$n"
    if (-not (Test-Path $src)) { throw "$src is missing - did scp of the certs fail?" }
    Move-Item -Path $src -Destination (Join-Path $CertDir $n) -Force
}

# server.key impersonates this box; ca.crt is public but must not be swappable by a non-admin
# (swap the CA and you choose which clients are trusted). Administrators + SYSTEM only.
icacls $CertDir /inheritance:r /grant "Administrators:(OI)(CI)F" /grant "SYSTEM:(OI)(CI)F" | Out-Null

# NOTE: ca.key is deliberately NOT here and must never be. It mints client identities; it lives
# on the laptop only. This box has no business being able to issue new trading credentials.

# --- 3. venv ------------------------------------------------------------------
# Rebuild rather than reuse: requirements.txt gained numpy, which used to arrive only as a
# TRANSITIVE dependency of MetaTrader5. A reused venv would silently miss a new dep, and the
# ImportError would surface inside a Scheduled Task, where nobody is watching stdout.
Step "Rebuilding the venv"
if (-not (Test-Path $PY)) {
    python -m venv (Join-Path $AppDir ".venv")
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}
& $PY -m pip install --upgrade pip --quiet
& $PY -m pip install -r (Join-Path $AppDir "requirements.txt") --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# Prove the import graph actually resolves on THIS box, now -- not at 3am inside a scheduled task.
& $PY -c "import numpy, fastapi, uvicorn, MetaTrader5; print('  imports OK')"
if ($LASTEXITCODE -ne 0) { throw "the app's imports do not resolve in the venv" }

# --- 4. The server task -------------------------------------------------------
# The token is read from the machine environment if install_server.ps1 already set one; otherwise
# a fresh one is generated. Either way it is written into the wrapper the task executes -- NOT
# relied upon via inheritance, because a Scheduled Task inherits the Schedule service's
# environment block, which was captured at BOOT. That would fail silently AND fail OPEN: a blank
# XAUORDERPAD_TOKEN disables authentication outright (config.py defaults it to "").
$token = [Environment]::GetEnvironmentVariable("XAUORDERPAD_TOKEN", "Machine")
if ([string]::IsNullOrWhiteSpace($token)) {
    # RNGCryptoServiceProvider, NOT RandomNumberGenerator::Fill(): the box runs Windows
    # PowerShell 5.1 on .NET Framework, where Fill() does not exist (it is .NET Core 2.1+).
    # And NOT Get-Random -- that is a seeded PRNG, not a CSPRNG, and this token authorises
    # order placement on a live account.
    $bytes = New-Object byte[] 24
    $rng = New-Object System.Security.Cryptography.RNGCryptoServiceProvider
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    $token = [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_').TrimEnd('=')
    [Environment]::SetEnvironmentVariable("XAUORDERPAD_TOKEN", $token, "Machine")
    $fresh = $true
}

# --- 4b. Locate the MT5 terminal ---------------------------------------------
# config.py ships from the developer's LAPTOP, where MT5_PATH is blank and LAUNCH_BROWSER is on.
# Both are wrong here, and both fail quietly: a blank MT5_PATH gives mt5.initialize() nothing to
# launch (every login then dies with an IPC timeout that reads like a broker fault), and
# LAUNCH_BROWSER=True made this headless server start Microsoft Edge on every restart.
#
# So the box's values are injected as ENVIRONMENT into the wrapper below -- not edited into the
# file, which the next ship would overwrite again.
$term = @(
    "C:\Program Files\MetaTrader 5\terminal64.exe",
    "C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $term) {
    $found = Get-ChildItem -Path $env:ProgramFiles -Filter terminal64.exe -Recurse -ErrorAction SilentlyContinue |
             Select-Object -First 1
    if ($found) { $term = $found.FullName }
}
if (-not $term) {
    throw "terminal64.exe not found under Program Files. The server would start, answer HTTP 200, and be unable to log in to ANY account -- a healthy-looking dead server. Install MT5 first."
}
Write-Host "  MT5 terminal: $term"

Step "Writing $WRAPPER"
# $wrapperBody, NOT $wrapper: PowerShell variable names are CASE-INSENSITIVE, so `$wrapper`
# and `$WRAPPER` are the SAME variable. Naming the here-string $wrapper silently overwrote the
# destination path with the file's own contents, and the write then failed with "Illegal
# characters in path" -- a message that points nowhere near the actual mistake.
$wrapperBody = @"
# GENERATED by deploy/ship.ps1. Do not hand-edit; re-run the shipper.
#
# HOST stays 127.0.0.1 ON PURPOSE. Caddy terminates TLS + mutual auth on 8443 and proxies here.
# Keeping uvicorn on loopback makes the deployment FAIL CLOSED: if Caddy is down or misconfigured,
# the trading API is unreachable rather than reachable without TLS.
`$env:XAUORDERPAD_HOST  = '127.0.0.1'
`$env:XAUORDERPAD_PORT  = '$Port'
`$env:XAUORDERPAD_TOKEN = '$token'

# The two settings that MUST differ from the developer's laptop. They live here, in the
# environment, because config.py itself is overwritten by every `ship`.
#   - no browser: this is a headless server. It was launching Edge on every restart.
#   - MT5_PATH: without it mt5.initialize() has no terminal to launch and every login
#     fails with an IPC timeout that looks like a broker outage.
`$env:XAUORDERPAD_LAUNCH_BROWSER = '0'
`$env:XAUORDERPAD_MT5_PATH       = '$term'

Set-Location '$AppDir'
& '$PY' '$AppDir\server.py'
"@
# [IO.File]::WriteAllText, not Set-Content -Encoding: on this box (Windows PowerShell 5.1,
# Server 2022) Set-Content rejects -Encoding with "A parameter cannot be found", even though
# Get-Command reports the parameter exists -- something shadows the cmdlet in script scope.
# The .NET call is also strictly better here: it is deterministic and writes NO BOM, and a BOM
# at the top of a generated .ps1 is its own subtle bug.
[System.IO.File]::WriteAllText($WRAPPER, $wrapperBody, (New-Object System.Text.ASCIIEncoding))

Step "Registering the '$TASK' task"
# Register-ScheduledTask, never `schtasks /TR "..."` -- the latter mangles quoting through
# PowerShell and fails SILENTLY (user_data.ps1:50-53 learned this for ec2-autostop).
$act = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-ExecutionPolicy Bypass -NonInteractive -WindowStyle Hidden -File `"$WRAPPER`""
# Runs INSIDE the autologon desktop session (LogonType Interactive), NOT session 0 (was S4U). S4U has no
# interactive desktop/profile -- and that is what made MT5 broker logins hang ~60s on an IPC timeout and
# spawned a second, invisible terminal64. AtLogOn (not AtStartup) because an Interactive task fires when
# Administrator logs on, which Windows autologon does automatically at boot (see `mt5_ec2.py autologon`).
# Prerequisite: autologon MUST be configured, or this task never runs at boot (no logon -> no trigger).
$trg = New-ScheduledTaskTrigger -AtLogOn -User "Administrator"
$prn = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType Interactive -RunLevel Highest
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TASK -Action $act -Trigger $trg -Principal $prn -Settings $set -Force | Out-Null
if (-not (Get-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue)) {
    throw "FATAL: the '$TASK' task was not created."
}

try { Start-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue } catch {}

# --- 5. Prove it is listening -- on LOOPBACK, and nowhere else ----------------
# The task now runs at Administrator LOGON in the interactive desktop session (Interactive principal
# above), so it only starts once autologon has logged Administrator in. When shipping BEFORE the
# autologon reboot there is no interactive session yet, so it won't be listening -- that is EXPECTED,
# not a failure. If it IS up (post-reboot, or an old instance still running) we still assert the
# fail-closed loopback binding; otherwise we defer verification to the post-reboot `health` check.
$conn = $null
foreach ($i in 1..20) {
    Start-Sleep -Seconds 1
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) { break }
}
if ($conn) {
    # Assert the WHOLE SET of listeners is loopback, not just the first. A SECOND binding on this port
    # sitting wide open on 0.0.0.0 is exactly what this catches. Any non-loopback address is a
    # fail-closed violation.
    $addrs = @($conn | ForEach-Object { $_.LocalAddress } | Sort-Object -Unique)
    Write-Host "  uvicorn listening on $($addrs -join ', '):$Port"
    $bad = $addrs | Where-Object { $_ -notin @("127.0.0.1", "::1") }
    if ($bad) {
        throw "FAIL-CLOSED VIOLATED: uvicorn is listening on $($bad -join ', ') (not only loopback). Reachable WITHOUT TLS or client-cert auth. Stop the task and investigate."
    }
} else {
    Write-Host "  NOTE: '$TASK' is registered but not yet listening -- it starts at the Administrator logon."
    Write-Host "        REBOOT the box (autologon), then verify with:  python mt5_ec2.py health"
}

Write-Host ""
if ($fresh) { Write-Host "  API token (generated; type this into the app):  $token" }
else        { Write-Host "  API token: unchanged (already set in the machine environment)" }
Write-Host ""
Write-Host "  Next: caddy_setup.ps1 puts the mTLS front door on 8443."
Write-Host ""
