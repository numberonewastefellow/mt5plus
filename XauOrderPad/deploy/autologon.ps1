<#
    Run ON THE BOX by `mt5_ec2.py autologon`. Turns the headless session-0 box into one that boots
    into an interactive Administrator DESKTOP, because MT5 broker logins hang ~60s on an IPC timeout
    when the terminal/server run under S4U/session 0 (no interactive profile). In a real logged-in
    desktop they're instant, AutoTrading (a per-terminal GUI setting) can be enabled once and persists,
    and boot auto-login from accounts.dat "just works".

    What it does:
      1. Windows autologon via Sysinternals Autologon -> the password is stored as an ENCRYPTED LSA
         secret, NOT a plaintext DefaultPassword registry value.
      2. A Startup shortcut for terminal64.exe so MT5 auto-launches (and auto-logs-in) in the session.

    The password is NOT baked into this file: `mt5_ec2.py` scp's it to C:\app\_autologon_pw.txt right
    before this runs; we read it, hand it to Autologon, then delete the temp file.
#>
$ErrorActionPreference = "Stop"

$pwFile = "C:\app\_autologon_pw.txt"
if (-not (Test-Path $pwFile)) { throw "password file $pwFile missing -- mt5_ec2.py should have scp'd it" }
$pw = (Get-Content $pwFile -Raw).Trim()

# --- 1. Autologon (Sysinternals -> encrypted LSA secret) ----------------------
$exe = "C:\app\Autologon64.exe"
if (-not (Test-Path $exe)) {
    Write-Host "Downloading Sysinternals Autologon ..."
    Invoke-WebRequest -Uri "https://live.sysinternals.com/Autologon64.exe" -OutFile $exe -UseBasicParsing
}
Write-Host "Configuring autologon for Administrator ..."
& $exe -accepteula "Administrator" $env:COMPUTERNAME $pw | Out-Null

# Verify it took. Autologon sets AutoAdminLogon=1 and writes the encrypted secret; a plaintext
# DefaultPassword must NOT be present.
$wl  = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
$aal = (Get-ItemProperty -Path $wl -Name AutoAdminLogon -ErrorAction SilentlyContinue).AutoAdminLogon
if ("$aal" -ne "1") { throw "Autologon did not set AutoAdminLogon=1 (got '$aal')" }
$plain = (Get-ItemProperty -Path $wl -Name DefaultPassword -ErrorAction SilentlyContinue).DefaultPassword
if ($plain) { Write-Host "  WARNING: a plaintext DefaultPassword is present -- Autologon should have used the LSA secret" }
Write-Host "  AutoAdminLogon = 1 (password stored as an encrypted LSA secret)"

# --- 2. MT5 Startup shortcut (auto-launch + auto-login in the desktop session) -
$mt5     = "C:\Program Files\MetaTrader 5\terminal64.exe"
$startup = [Environment]::GetFolderPath("Startup")    # Administrator's Startup folder
$lnk     = Join-Path $startup "MetaTrader5.lnk"
if (Test-Path $mt5) {
    $sh = New-Object -ComObject WScript.Shell
    $s  = $sh.CreateShortcut($lnk)
    $s.TargetPath       = $mt5
    $s.WorkingDirectory = "C:\Program Files\MetaTrader 5"
    $s.Save()
    Write-Host "  MT5 Startup shortcut -> $lnk"
} else {
    Write-Host "  WARNING: $mt5 not found -- skipped the MT5 Startup shortcut. Adjust the path if MT5 lives elsewhere."
}

# --- 3. Delete the transient password file ------------------------------------
Remove-Item $pwFile -Force -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "Done. REBOOT the box, then RDP in ONCE to:"
Write-Host "  - Tools > Options > Expert Advisors > 'Allow algorithmic trading' + toolbar AutoTrading ON"
Write-Host "  - tick 'Save password' on the broker login"
Write-Host "Both persist across reboots. After that the box boots straight into a logged-in, AutoTrading-on MT5."
