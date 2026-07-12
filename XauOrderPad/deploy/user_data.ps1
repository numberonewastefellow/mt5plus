<powershell>
# Bootstrap for the MT5 + XauOrderPad box. Runs once, on first boot (EC2Launch v2).
# Everything is logged to C:\bootstrap.log; C:\bootstrap-done.txt is written last so
# `mt5_ec2.py status` can tell whether this finished.

$ErrorActionPreference = "Continue"
Start-Transcript -Path "C:\bootstrap.log" -Append

function Step($msg) { Write-Host "=== $msg ===" }

# --- 1. OpenSSH Server (for the 8765 tunnel + scp) ------------------------
Step "Installing OpenSSH Server"
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd

# Default shell = PowerShell (nicer than cmd for remote commands)
New-Item -Path "HKLM:\SOFTWARE\OpenSSH" -Force | Out-Null
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell `
  -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force | Out-Null

# Key-based auth for Administrator. Windows sshd reads this ONE file for all admins,
# and refuses it unless the ACL is exactly Administrators+SYSTEM.
Step "Installing authorized key"
$akf = "C:\ProgramData\ssh\administrators_authorized_keys"
Set-Content -Path $akf -Value "{{SSH_PUBKEY}}" -Encoding ascii
icacls $akf /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
Restart-Service sshd

# --- 2. Python 3.12 (MetaTrader5 wheel is cp312) --------------------------
Step "Installing Python 3.12"
$py = "C:\Windows\Temp\python-installer.exe"
Invoke-WebRequest -Uri "{{PYTHON_URL}}" -OutFile $py -UseBasicParsing
Start-Process -FilePath $py -ArgumentList "/quiet","InstallAllUsers=1","PrependPath=1","Include_pip=1" -Wait
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine")

# --- 3. MetaTrader 5 terminal --------------------------------------------
# Silent install. Broker login is done interactively over RDP afterwards (we do not
# bake credentials into user_data - it is readable via the metadata service).
Step "Downloading MT5 setup"
$mt5 = "C:\mt5setup.exe"
Invoke-WebRequest -Uri "{{MT5_SETUP_URL}}" -OutFile $mt5 -UseBasicParsing
Step "Installing MT5 (silent)"
Start-Process -FilePath $mt5 -ArgumentList "/auto" -Wait

# --- 4. Auto-stop -------------------------------------------------------------
# ONSTART so it re-arms on every boot (user_data itself only runs once).
# InstanceInitiatedShutdownBehavior=stop turns this shutdown into an EC2 *stop*.
#
# Use Register-ScheduledTask, NOT `schtasks /TR "..."`. Passing a /TR value that
# itself contains spaces and quotes through PowerShell to a native exe mangles the
# quoting and schtasks fails *silently* - the task is simply never created, and you
# only find out when the box never stops and quietly eats the credit budget.
Step "Registering {{AUTO_STOP_MINUTES}}-minute auto-stop"
$secs = {{AUTO_STOP_SECONDS}}
$act = New-ScheduledTaskAction -Execute "shutdown.exe" -Argument "/s /t $secs"
$trg = New-ScheduledTaskTrigger -AtStartup
$prn = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName "ec2-autostop" -Action $act -Trigger $trg -Principal $prn -Force | Out-Null
if (-not (Get-ScheduledTask -TaskName "ec2-autostop" -ErrorAction SilentlyContinue)) {
    Write-Host "FATAL: ec2-autostop task was not created - box will not auto-stop!"
}
# Arm it for this first boot too (ONSTART won't fire until the next reboot).
& shutdown /s /t $secs

# sshd cached the machine PATH from before Python was installed; restart it so
# `python` resolves in SSH sessions without waiting for a reboot.
Restart-Service sshd

# --- 5. App working dir --------------------------------------------------
New-Item -ItemType Directory -Force -Path "C:\app" | Out-Null

Step "Bootstrap complete"
"done $(Get-Date -Format o)" | Set-Content -Path "C:\bootstrap-done.txt" -Encoding ascii
Stop-Transcript
</powershell>
<persist>false</persist>
