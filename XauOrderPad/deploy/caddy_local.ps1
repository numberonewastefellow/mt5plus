<#
    Local (LAN) mTLS front door for XauOrderPad -- FOREGROUND runner for THIS PC.

    This is the desktop twin of caddy_setup.ps1 (which runs ON THE BOX as a SYSTEM scheduled task).
    The security posture is identical -- validate the config, prove `require_and_verify` is present,
    and prove a certificate-less client is rejected -- but here Caddy runs in the FOREGROUND of its
    own window, and it uses the LAN cert (certs-lan/, SAN = the LAN IP), never the EC2 cert (certs/,
    SAN = the Elastic IP).

    Called by `start_server.bat tls` AFTER uvicorn is up on 127.0.0.1:8765.
#>
[CmdletBinding()]
param(
    [int]$TlsPort = 8443,
    [int]$AppPort = 8765,
    [string]$Caddyfile = "Caddyfile.lan"
)

$ErrorActionPreference = "Stop"
function Step($m) { Write-Host "==> $m" }

# Run from deploy/ so the RELATIVE paths in Caddyfile.lan (./certs-lan, ./certs, ./logs) resolve
# no matter where the repo lives.
$DEPLOY = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $DEPLOY
$CADDY = Join-Path $DEPLOY "caddy.exe"
$CFG   = Join-Path $DEPLOY $Caddyfile
$API   = "https://api.github.com/repos/caddyserver/caddy/releases/latest"

# --- 0. the RIGHT cert must be present (LAN, not EC2) -------------------------
foreach ($n in @("certs-lan\server.crt", "certs-lan\server.key", "certs\ca.crt")) {
    if (-not (Test-Path (Join-Path $DEPLOY $n))) {
        throw "$n is missing. Mint the LAN cert first:`n" +
              "    python make_certs.py --ip <lan ip> --server-only --out certs-lan"
    }
}
if (-not (Test-Path $CFG)) { throw "$CFG is missing." }
New-Item -ItemType Directory -Force -Path (Join-Path $DEPLOY "logs") | Out-Null

# --- 1. caddy.exe (download once) --------------------------------------------
if (Test-Path $CADDY) {
    Step "caddy.exe already present"
} else {
    Step "Resolving the latest Caddy release"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $rel = Invoke-RestMethod -Uri $API -UseBasicParsing -Headers @{ "User-Agent" = "xauorderpad-deploy" }
    $asset = $rel.assets | Where-Object { $_.name -like "*windows_amd64.zip" } | Select-Object -First 1
    if (-not $asset) { throw "No windows_amd64 asset in Caddy release $($rel.tag_name)." }
    Write-Host "  $($asset.name)  ($($rel.tag_name))"
    Step "Downloading Caddy"
    $zip = Join-Path $DEPLOY "caddy.zip"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath (Join-Path $DEPLOY "_caddy") -Force
    Move-Item (Join-Path $DEPLOY "_caddy\caddy.exe") $CADDY -Force
    Remove-Item $zip, (Join-Path $DEPLOY "_caddy") -Recurse -Force -ErrorAction SilentlyContinue
}
if (-not (Test-Path $CADDY)) { throw "caddy.exe is not at $CADDY" }

# --- 2. validate config, and PROVE the enforcing directive is present --------
Step "Validating $Caddyfile"
& $CADDY validate --config $CFG --adapter caddyfile
if ($LASTEXITCODE -ne 0) { throw "The Caddyfile is invalid. NOT starting: a bad config could serve without client-cert auth." }
if (-not (Select-String -Path $CFG -Pattern "require_and_verify" -Quiet)) {
    throw "SECURITY: 'require_and_verify' is not in $Caddyfile. Without it, port $TlsPort is an unauthenticated path to the trading API. Refusing to start."
}

# --- 3. firewall (best-effort; needs an elevated shell) ----------------------
try {
    Remove-NetFirewallRule -DisplayName "XauOrderPad LAN mTLS" -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName "XauOrderPad LAN mTLS" -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $TlsPort -Profile Any -ErrorAction Stop | Out-Null
    Step "firewall: allowed $TlsPort/tcp inbound"
} catch {
    Write-Host "   (could not add a firewall rule -- run as admin if the phone times out): $($_.Exception.Message)"
}

# --- 4. run Caddy (background), then self-test, then hand the window over -----
Step "Starting Caddy on $TlsPort"
$p = Start-Process -FilePath $CADDY `
        -ArgumentList @("run", "--config", $CFG, "--adapter", "caddyfile") `
        -WorkingDirectory $DEPLOY -PassThru -NoNewWindow
$conn = $null
foreach ($i in 1..25) {
    Start-Sleep -Milliseconds 700
    $conn = Get-NetTCPConnection -LocalPort $TlsPort -State Listen -ErrorAction SilentlyContinue
    if ($conn) { break }
}
if (-not $conn) { throw "Caddy is not listening on $TlsPort after ~18s." }
Write-Host "  Caddy listening on $TlsPort"

# The invariant the whole design rests on: uvicorn must be loopback-only, or $AppPort bypasses mTLS.
$app = Get-NetTCPConnection -LocalPort $AppPort -State Listen -ErrorAction SilentlyContinue
if ($app) {
    $addrs = @($app | ForEach-Object { $_.LocalAddress } | Sort-Object -Unique)
    $bad = $addrs | Where-Object { $_ -notin @("127.0.0.1", "::1") }
    if ($bad) {
        Write-Host "  *** WARNING: uvicorn is on $($bad -join ', ') -- port $AppPort BYPASSES mTLS in cleartext." -ForegroundColor Yellow
        Write-Host "      Restart uvicorn loopback-only (start_server.bat tls sets HOST=127.0.0.1)." -ForegroundColor Yellow
    } else {
        Write-Host "  uvicorn is loopback-only ($($addrs -join ', ')) -- the proxy is the only way in"
    }
} else {
    Write-Host "  NOTE: nothing is listening on $AppPort yet -- start uvicorn (start_server.bat tls)."
}

# Without a client certificate we MUST be rejected. If this SUCCEEDS, client auth is not being
# enforced. The rejection happens at the TLS handshake (require_and_verify), so this is valid even
# before uvicorn is up. Raw SslStream because Windows PowerShell 5.1 has no -SkipCertificateCheck.
Step "Self-test: connecting with NO client certificate (this MUST fail)"
$served = $false
$tcp = $null; $ssl = $null
try {
    $tcp = New-Object System.Net.Sockets.TcpClient("127.0.0.1", $TlsPort)
    $ssl = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false,
        ([System.Net.Security.RemoteCertificateValidationCallback] { $true }))
    $ssl.AuthenticateAsClient("127.0.0.1")
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
    try { Stop-Process -Id $p.Id -Force } catch { }
    throw "SECURITY FAILURE: the proxy answered '$reply' to a client with NO certificate. mTLS is NOT being enforced -- port $TlsPort is an open path to the trading API. Caddy stopped."
}

Write-Host ""
Write-Host "  LAN mTLS front door is UP on $TlsPort."
Write-Host "  Phone -> https://192.168.0.116:$TlsPort  (ca.crt + client.p12 already imported; same CA as EC2)."
Write-Host "  Press Ctrl+C to stop."
Write-Host ""
Wait-Process -Id $p.Id
