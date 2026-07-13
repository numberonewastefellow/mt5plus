<#
    Install + run Caddy as the mTLS front door. Runs ON THE BOX, pushed by `mt5_ec2.py caddy`.

    Caddy is a single .exe -- no installer, no service framework, no runtime. It listens on 8443,
    demands a client certificate signed by our private CA, and proxies to uvicorn on loopback.

    Why a proxy at all, rather than TLS inside uvicorn:
      uvicorn binds exactly ONE address. If it enforced mTLS, the browser reaching this box through
      the SSH tunnel (127.0.0.1:8765) would need a client certificate too, breaking the desktop
      flow that works today. Terminating TLS out here keeps server.py completely unchanged, keeps
      the tunnel working, and -- because uvicorn stays on loopback -- makes the whole thing FAIL
      CLOSED if this proxy ever stops.
#>
[CmdletBinding()]
param(
    [string]$CertDir = "C:\app\certs",
    [int]$TlsPort = 8443,
    [int]$AppPort = 8765
)

$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "==> $m" }

$TASK = "xauorderpad-caddy"
$CADDY = "C:\app\caddy.exe"
$CADDYFILE = "C:\app\Caddyfile"
$API = "https://api.github.com/repos/caddyserver/caddy/releases/latest"

foreach ($n in @("server.crt", "server.key", "ca.crt")) {
    if (-not (Test-Path (Join-Path $CertDir $n))) {
        throw "$CertDir\$n is missing. Run 'mt5_ec2.py ship' first (it installs the TLS material)."
    }
}
if (-not (Test-Path $CADDYFILE)) { throw "$CADDYFILE is missing - did scp fail?" }

# The Caddyfile writes a per-site access log to C:\app\logs. Caddy does not create the directory,
# and a missing log path makes it fail to start -- create it up front.
New-Item -ItemType Directory -Force -Path "C:\app\logs" | Out-Null

# --- 1. caddy.exe -------------------------------------------------------------
if (Test-Path $CADDY) {
    Step "caddy.exe already present - skipping download"
} else {
    Step "Resolving the latest Caddy release"
    # NOT .../releases/latest/download/caddy_windows_amd64.zip -- that pattern only works when the
    # asset filename is version-independent, and Caddy's is not: it ships
    # caddy_<version>_windows_amd64.zip, so the static URL 404s. Ask the API for the real asset.
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $rel = Invoke-RestMethod -Uri $API -UseBasicParsing -Headers @{ "User-Agent" = "xauorderpad-deploy" }
    $asset = $rel.assets | Where-Object { $_.name -like "*windows_amd64.zip" } | Select-Object -First 1
    if (-not $asset) { throw "No windows_amd64 asset in Caddy release $($rel.tag_name)." }
    Write-Host "  $($asset.name)  ($($rel.tag_name))"

    Step "Downloading Caddy"
    $zip = "C:\app\caddy.zip"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath "C:\app\_caddy" -Force
    Move-Item "C:\app\_caddy\caddy.exe" $CADDY -Force
    Remove-Item $zip, "C:\app\_caddy" -Recurse -Force -ErrorAction SilentlyContinue
}
if (-not (Test-Path $CADDY)) { throw "caddy.exe is not at $CADDY" }

# --- 2. Validate the config BEFORE we run it ----------------------------------
# A Caddyfile typo here does not fail loudly at runtime -- Caddy can start and serve a site with
# NO client_auth block, i.e. an open TCP path to a live-trading API. Validate first, and refuse to
# proceed if the file does not parse.
Step "Validating the Caddyfile"
& $CADDY validate --config $CADDYFILE --adapter caddyfile
if ($LASTEXITCODE -ne 0) { throw "The Caddyfile is invalid. NOT starting: a bad config could serve without client-cert auth." }

# Belt and braces: prove the directive that does the actual enforcing is still in the file.
if (-not (Select-String -Path $CADDYFILE -Pattern "require_and_verify" -Quiet)) {
    throw "SECURITY: 'require_and_verify' is not in the Caddyfile. Without it, port $TlsPort is an unauthenticated path to the trading API. Refusing to start."
}

# --- 3. Windows firewall ------------------------------------------------------
# The EC2 security group is one gate; the Windows firewall is the other, and it silently drops
# 8443 by default. Both must allow it or the phone times out with no clue why.
Step "Allowing $TlsPort through the Windows firewall"
Remove-NetFirewallRule -DisplayName "XauOrderPad mTLS" -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "XauOrderPad mTLS" -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort $TlsPort -Profile Any | Out-Null

# --- 4. Run it as a task ------------------------------------------------------
Step "Registering the '$TASK' task"
try { Stop-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue } catch { }

$act = New-ScheduledTaskAction -Execute $CADDY `
    -Argument "run --config `"$CADDYFILE`" --adapter caddyfile" -WorkingDirectory "C:\app"
$trg = New-ScheduledTaskTrigger -AtStartup
$prn = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TASK -Action $act -Trigger $trg -Principal $prn -Settings $set -Force | Out-Null
if (-not (Get-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue)) {
    throw "FATAL: the '$TASK' task was not created."
}
Start-ScheduledTask -TaskName $TASK

# --- 5. Prove it -------------------------------------------------------------
$conn = $null
foreach ($i in 1..20) {
    Start-Sleep -Seconds 1
    $conn = Get-NetTCPConnection -LocalPort $TlsPort -State Listen -ErrorAction SilentlyContinue
    if ($conn) { break }
}
if (-not $conn) { throw "Caddy is not listening on $TlsPort after 20s." }
Write-Host "  Caddy listening on $TlsPort"

# The invariant that matters more than Caddy being up: uvicorn must STILL be loopback-only.
$app = Get-NetTCPConnection -LocalPort $AppPort -State Listen -ErrorAction SilentlyContinue
if ($app) {
    # The whole set, not -First 1: a second binding on 0.0.0.0:$AppPort would bypass mTLS while a
    # first-only check reported "loopback-only" and passed.
    $addrs = @($app | ForEach-Object { $_.LocalAddress } | Sort-Object -Unique)
    $bad = $addrs | Where-Object { $_ -notin @("127.0.0.1", "::1") }
    if ($bad) {
        throw "FAIL-CLOSED VIOLATED: uvicorn is on $($bad -join ', ') (not only loopback) -- port $AppPort bypasses mTLS entirely."
    }
    Write-Host "  uvicorn still loopback-only ($($addrs -join ', '):$AppPort) - the proxy is the only way in"
}

# Locally, WITHOUT a client cert, we must be rejected. This is the real test: if this SUCCEEDS,
# client auth is not being enforced and the deployment is wide open.
#
# Done with a raw SslStream rather than Invoke-WebRequest: -SkipCertificateCheck is PowerShell 7
# only and this box runs Windows PowerShell 5.1, where it is a parameter-binding error -- which
# would make the security self-test fail for the WRONG reason and look like a pass if anyone
# loosened the catch. A raw handshake also tests the thing we actually care about.
Step "Self-test: connecting with NO client certificate (this MUST fail)"
$served = $false
$tcp = $null; $ssl = $null
try {
    $tcp = New-Object System.Net.Sockets.TcpClient("127.0.0.1", $TlsPort)
    # Accept ANY server cert here: we are testing client auth, not server trust.
    $ssl = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false,
        ([System.Net.Security.RemoteCertificateValidationCallback] { $true }))
    $ssl.AuthenticateAsClient("127.0.0.1")     # deliberately presenting NO client certificate

    # Under TLS 1.2 the handshake above already throws. Under TLS 1.3 the client believes the
    # handshake succeeded and the rejection only surfaces on the first read -- so we must
    # actually try to get a response before concluding anything.
    $req = [Text.Encoding]::ASCII.GetBytes("GET /api/config HTTP/1.1`r`nHost: 127.0.0.1`r`nConnection: close`r`n`r`n")
    $ssl.Write($req, 0, $req.Length); $ssl.Flush()
    $ssl.ReadTimeout = 8000
    $buf = New-Object byte[] 64
    $n = $ssl.Read($buf, 0, $buf.Length)
    if ($n -gt 0) {
        $head = [Text.Encoding]::ASCII.GetString($buf, 0, $n)
        if ($head -like "HTTP/*") { $served = $true; $reply = $head.Split("`r")[0] }
    }
} catch {
    Write-Host "  rejected, as required ($($_.Exception.InnerException.GetType().Name))"
} finally {
    if ($ssl) { $ssl.Dispose() }
    if ($tcp) { $tcp.Dispose() }
}
if ($served) {
    throw "SECURITY FAILURE: the proxy answered '$reply' to a client with NO certificate. mTLS is NOT being enforced -- port $TlsPort is an open path to the trading API. Fix the Caddyfile before going further."
}

Write-Host ""
Write-Host "  The mTLS front door is up on $TlsPort."
Write-Host "  The phone needs: ca.crt (trust) + client.p12 (its identity) + the API token."
Write-Host ""
