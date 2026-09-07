<#
.SYNOPSIS
    Reset the Exness DEMO account balance to a fixed amount (default $1).

.DESCRIPTION
    RecoveryGridScalper is tested from a $1 base, over and over. This replays the
    same request the Exness personal area makes when you click "Set balance".

    *** THIS SCRIPT CONTAINS NO CREDENTIALS AND MUST NEVER CONTAIN ANY. ***

    The session lives in ONE file outside the repo:
        %USERPROFILE%\Documents\MilkyAppData\MyCred\exness_session.txt
    It is a curl CONFIG file holding a single line:
        header = "Cookie: country=AE; cf_clearance=...; JWT=eyJ..."

    Why a config file and not -H on the command line: a command line is readable
    by every process on the machine (Task Manager's "Command line" column,
    Get-CimInstance Win32_Process, PowerShell history, console scrollback). A
    --config file keeps the token out of all of them.

    THE SESSION EXPIRES ABOUT EVERY 6 HOURS. That is not a bug to work around -
    it is the point of the expiry check below, which refuses to send and tells
    you to re-capture instead of failing with an opaque 403.

.PARAMETER Amount
    Balance to set. Default 1. Above 100 is refused unless -Force, so a
    fat-finger cannot set 100000.

.PARAMETER ImportCurl
    Path to a saved "Copy as cURL" dump from the browser. Extracts ONLY the
    Cookie header from it, writes the cred file, locks its permissions and
    prints the new expiry. This is how you refresh every ~6 hours.

.PARAMETER DeleteSource
    With -ImportCurl, delete the dump file after a successful import (it holds
    the same token).

.EXAMPLE
    .\reset_demo_balance.ps1
    .\reset_demo_balance.ps1 -Amount 10
    .\reset_demo_balance.ps1 -ImportCurl "$env:TEMP\exness.txt" -DeleteSource
#>
[CmdletBinding()]
param(
    [double]$Amount = 1,
    [string]$ImportCurl = "",
    [switch]$DeleteSource,
    [switch]$Force
)

$ErrorActionPreference = "Stop"

# --- Constants. The account is HARD-WIRED on purpose -----------------------
#
# There is deliberately no -Account parameter. This script exists to reset one
# known demo account; a switch that lets it point somewhere else is a switch
# that can point it at real money by accident.
$ACCOUNT   = "472627873"                    # Exness-MT5Trial16 - DEMO
$ACCOUNT_D = "Exness-MT5Trial16 (DEMO)"
$URL       = "https://my.exness.com/v4/tam2/accounts/$ACCOUNT/set_balance"
$MAX_SAFE  = 100.0

# The cf_clearance cookie is bound to IP *and* User-Agent. If this string stops
# matching the browser the token was captured from, Cloudflare re-challenges and
# you get a 403 that looks like an auth failure but is not.
$UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0"

$CRED = Join-Path $env:USERPROFILE "Documents\MilkyAppData\MyCred\exness_session.txt"

# --- Helpers ---------------------------------------------------------------

# Nothing reaches the console without passing through here. An unexpected error
# dump that echoes the request headers must not print a usable token.
function Hide-Secrets([string]$s) {
    if ([string]::IsNullOrEmpty($s)) { return "" }
    $s = [regex]::Replace($s, 'eyJ[A-Za-z0-9_\-]{8,}(\.[A-Za-z0-9_\-]+){0,2}', '<redacted-jwt>')
    $s = [regex]::Replace($s, 'cf_clearance=[^;"''\s]+', 'cf_clearance=<redacted>')
    return $s
}

function Say([string]$msg, [string]$color = "Gray") {
    Write-Host (Hide-Secrets $msg) -ForegroundColor $color
}

function Show-Recipe {
    Say ""
    Say "How to (re)capture the session - takes about 20 seconds:" Cyan
    Say "  1. Firefox -> https://my.exness.com/pa/trading/accounts  (log in)"
    Say "  2. F12 -> Network tab"
    Say "  3. Click 'Set balance' on the demo account and submit it once"
    Say "  4. Right-click the 'set_balance' request -> Copy -> Copy as cURL"
    Say "  5. Paste into a scratch file, e.g. %TEMP%\exness.txt"
    Say "  6. reset_demo_balance.bat -ImportCurl %TEMP%\exness.txt -DeleteSource"
    Say ""
    Say "  The import keeps ONLY the Cookie header. Everything else is discarded." DarkGray
}

# Lock the cred file to the current user. Idempotent, run every time, so a file
# created by hand with default permissions heals itself.
function Protect-CredFile([string]$path) {
    try {
        $me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
        & icacls.exe $path /inheritance:r /grant:r "${me}:(F)" 2>&1 | Out-Null
    } catch {
        Say "  warning: could not tighten permissions on the cred file ($($_.Exception.Message))" Yellow
    }
}

# base64url -> JSON. '-'/'_' are not base64, and the padding is stripped; both
# have to be put back or FromBase64String throws.
function Get-JwtExpiry([string]$jwt) {
    $parts = $jwt.Split('.')
    if ($parts.Count -lt 2) { return $null }
    $p = $parts[1].Replace('-', '+').Replace('_', '/')
    switch ($p.Length % 4) {
        2 { $p += '==' }
        3 { $p += '=' }
        1 { return $null }
    }
    try {
        $payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($p)) | ConvertFrom-Json
    } catch { return $null }
    if ($null -eq $payload.exp) { return $null }
    return [DateTimeOffset]::FromUnixTimeSeconds([long]$payload.exp).ToLocalTime()
}

function Get-CookieFromCredFile([string]$path) {
    foreach ($line in (Get-Content -LiteralPath $path)) {
        $m = [regex]::Match($line, '^\s*header\s*=\s*"Cookie:\s*(.+)"\s*$')
        if ($m.Success) { return $m.Groups[1].Value }
    }
    return $null
}

# --- ImportCurl ------------------------------------------------------------
#
# "Copy as cURL (cmd)" in Firefox/Chrome on Windows escapes the command for
# cmd.exe: quotes arrive as ^" and every |, %, $, {, } is prefixed with ^.
# Strip that first or the Cookie regex never matches. POSIX-style dumps (single
# quotes, backslash line continuations) are handled too.
function Import-CurlCookie([string]$src) {
    if (-not (Test-Path -LiteralPath $src)) {
        Say "ImportCurl: file not found: $src" Red
        exit 5
    }
    $raw = Get-Content -LiteralPath $src -Raw

    if ($raw.Contains('^"')) {
        $raw = $raw -replace '\^\r?\n', ''          # cmd line continuations
        $raw = $raw.Replace('^^', "`u{0001}")       # a literal ^ , parked
        $raw = [regex]::Replace($raw, '\^(.)', '$1')
        $raw = $raw.Replace("`u{0001}", '^')
    } else {
        $raw = $raw -replace '\\\r?\n', ''          # POSIX line continuations
    }

    $m = [regex]::Match($raw, '(?:-H|--header)\s+(["''])Cookie:\s*(.+?)\1', 'IgnoreCase')
    if (-not $m.Success) {
        Say "ImportCurl: no Cookie header found in that file." Red
        Say "  Make sure you used 'Copy as cURL' and not 'Copy URL'." DarkGray
        exit 5
    }
    $cookie = $m.Groups[2].Value.Trim()

    if ($raw -notmatch [regex]::Escape($ACCOUNT)) {
        Say "note: that capture is not for account $ACCOUNT - importing the session anyway," Yellow
        Say "      since the cookie is account-independent. The target stays $ACCOUNT." Yellow
    }

    $exp = $null
    $jm = [regex]::Match($cookie, '(?:^|;\s*)JWT=([A-Za-z0-9._\-]+)')
    if ($jm.Success) { $exp = Get-JwtExpiry $jm.Groups[1].Value }
    if ($null -eq $exp) {
        Say "ImportCurl: the cookie has no readable JWT. Nothing written." Red
        exit 5
    }
    if ($exp -le [DateTimeOffset]::Now) {
        Say "ImportCurl: that capture is ALREADY expired ($($exp.ToString('yyyy-MM-dd HH:mm:ss')))." Red
        Say "  Re-do the capture in the browser; the session must be fresh." DarkGray
        exit 3
    }

    $dir = Split-Path -Parent $CRED
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

    # Written WITHOUT a BOM: curl reads its config as bytes and a BOM makes the
    # first directive unparseable.
    $line = 'header = "Cookie: ' + $cookie + '"'
    [IO.File]::WriteAllText($CRED, $line + "`r`n", (New-Object Text.UTF8Encoding($false)))
    Protect-CredFile $CRED

    $left = $exp - [DateTimeOffset]::Now
    Say "Session imported." Green
    Say ("  file    : {0}" -f $CRED)
    Say ("  expires : {0}  ({1}h {2}m from now)" -f $exp.ToString('yyyy-MM-dd HH:mm:ss'), [int]$left.TotalHours, $left.Minutes)

    if ($DeleteSource) {
        Remove-Item -LiteralPath $src -Force
        Say "  source  : deleted ($src)" DarkGray
    } else {
        Say "  NOW DELETE $src - it holds the same token." Yellow
    }
    exit 0
}

# --- Main ------------------------------------------------------------------

if ($ImportCurl) { Import-CurlCookie $ImportCurl }

if ($Amount -le 0) {
    Say "Amount must be positive." Red
    exit 5
}
if ($Amount -gt $MAX_SAFE -and -not $Force) {
    Say ("Refusing to set {0:N2}: above the {1:N0} guard. Re-run with -Force if you meant it." -f $Amount, $MAX_SAFE) Red
    exit 5
}

if (-not (Test-Path -LiteralPath $CRED)) {
    Say "No session file at:" Red
    Say "  $CRED"
    Show-Recipe
    exit 2
}
Protect-CredFile $CRED

$cookie = Get-CookieFromCredFile $CRED
if (-not $cookie) {
    Say "The session file exists but has no 'header = `"Cookie: ...`"' line." Red
    Say "  Re-import it rather than editing by hand." DarkGray
    Show-Recipe
    exit 2
}

# --- The expiry gate. Refuse BEFORE sending. -------------------------------
$exp = $null
$jm = [regex]::Match($cookie, '(?:^|;\s*)JWT=([A-Za-z0-9._\-]+)')
if ($jm.Success) { $exp = Get-JwtExpiry $jm.Groups[1].Value }

if ($null -eq $exp) {
    Say "Could not read an expiry from the stored session (no JWT, or it is malformed)." Red
    Show-Recipe
    exit 3
}
$left = $exp - [DateTimeOffset]::Now
if ($left.TotalMinutes -lt 5) {
    if ($left.TotalSeconds -le 0) {
        Say ("Session EXPIRED at {0} - not sending anything." -f $exp.ToString('yyyy-MM-dd HH:mm:ss')) Red
    } else {
        Say ("Session expires in {0:N0} min (at {1}) - too close, not sending." -f $left.TotalMinutes, $exp.ToString('HH:mm:ss')) Red
    }
    Show-Recipe
    exit 3
}

# The endpoint now 400s ("ValidationError" on the balance field) unless the
# amount has two decimal places - "1" is rejected, "1.00" is accepted.
$amtStr = $Amount.ToString("F2", [cultureinfo]::InvariantCulture)
Say ""
Say "Reset demo balance" Cyan
Say ("  target  : {0}  {1}" -f $ACCOUNT, $ACCOUNT_D)
Say ("  amount  : {0}" -f $amtStr)
Say ("  session : valid until {0}  ({1}h {2}m left)" -f $exp.ToString('yyyy-MM-dd HH:mm:ss'), [int]$left.TotalHours, $left.Minutes)

$tmp = [IO.Path]::GetTempFileName()
$reqFile = [IO.Path]::GetTempFileName()
try {
    # The JSON body goes through a FILE, not an inline command-line argument.
    # An embedded `" (doubled-quote) reaches curl.exe's argv mangled - and not
    # the same way twice: Windows PowerShell 5.1 and pwsh 7's default native
    # argument passing re-escape it differently, and a backslash-escaped
    # version that satisfies one breaks under the other. The API answers 400
    # "Wrong data" on the whole request (not a named field) when this happens.
    # A file sidesteps command-line quoting entirely; curl's `-d`/`--data`
    # (unlike --data-raw) reads it via a leading @.
    $jsonBody = '{"balance":"' + $amtStr + '"}'
    [IO.File]::WriteAllText($reqFile, $jsonBody, (New-Object Text.UTF8Encoding($false)))

    # --config carries the Cookie; nothing secret is on this command line.
    $code = & curl.exe --config "$CRED" `
        -sS -X POST "$URL" `
        -H "Content-Type: application/json" `
        -H "Accept: application/json, text/plain, */*" `
        -H "User-Agent: $UA" `
        -H "Origin: https://my.exness.com" `
        -H "Referer: https://my.exness.com/pa/trading/accounts" `
        -H "X-G-Recaptcha-Platform: true" `
        --data "@$reqFile" `
        -o "$tmp" -w "%{http_code}" 2>&1

    $code = ($code | Out-String).Trim()
    $respBody = ""
    if (Test-Path -LiteralPath $tmp) { $respBody = (Get-Content -LiteralPath $tmp -Raw -ErrorAction SilentlyContinue) }
    if ($null -eq $respBody) { $respBody = "" }

    if ($code -eq "200" -or $code -eq "201" -or $code -eq "204") {
        Say ("  result  : OK - balance set to {0} on {1}" -f $amtStr, $ACCOUNT) Green
        if ($respBody.Trim()) { Say ("  response: {0}" -f $respBody.Trim()) DarkGray }
        Say "  (MT5 shows it within a second - the EA panel's 'acct : bal' line refreshes at 1 Hz.)" DarkGray
        exit 0
    }
    elseif ($code -eq "401" -or $code -eq "403") {
        Say ("  result  : HTTP {0} - the session or the Cloudflare clearance is stale." -f $code) Red
        Say "            (cf_clearance is bound to your IP and User-Agent, so a VPN or" DarkGray
        Say "             network change invalidates it even before the JWT expires.)" DarkGray
        if ($respBody.Trim()) { Say ("  response: {0}" -f $respBody.Trim()) DarkGray }
        Show-Recipe
        exit 4
    }
    else {
        Say ("  result  : HTTP {0}" -f $code) Red
        if ($respBody.Trim()) { Say ("  response: {0}" -f $respBody.Trim()) DarkGray }
        exit 4
    }
}
finally {
    if (Test-Path -LiteralPath $tmp) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $reqFile) { Remove-Item -LiteralPath $reqFile -Force -ErrorAction SilentlyContinue }
}
