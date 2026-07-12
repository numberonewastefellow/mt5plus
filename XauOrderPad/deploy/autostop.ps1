# Re-arm the auto-stop timer. Pushed to the box by `mt5_ec2.py autostop <minutes>`.
#
# The ec2-autostop task is registered ONSTART, so it re-arms on every boot - but with the
# seconds value baked into the task action at registration. user_data.ps1 sets that once, at
# create, and never runs again (<persist>false</persist>). Changing config.json does NOT move
# it. This script is the only thing that does.

$ErrorActionPreference = "Continue"
$secs = {{AUTO_STOP_SECONDS}}

# Cancel whatever the AtStartup task already armed for this boot.
& shutdown /a 2>$null

# Re-register so FUTURE boots use the new value too.
# Register-ScheduledTask, NOT `schtasks /TR "..."` - passing a /TR value containing spaces and
# quotes through PowerShell mangles the quoting and schtasks fails *silently*: the task is never
# created, and you only find out when the box never stops and quietly eats the credit budget.
$act = New-ScheduledTaskAction -Execute "shutdown.exe" -Argument "/s /t $secs"
$trg = New-ScheduledTaskTrigger -AtStartup
$prn = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName "ec2-autostop" -Action $act -Trigger $trg -Principal $prn -Force | Out-Null

# Arm it for THIS session - the AtStartup trigger will not fire again until the next boot.
& shutdown /s /t $secs

# Read the value back off the task rather than echoing what we meant to set.
$armed = (Get-ScheduledTask -TaskName ec2-autostop -ErrorAction SilentlyContinue).Actions[0].Arguments
if (-not $armed) {
    Write-Host "FATAL: ec2-autostop task is missing - this box will NOT auto-stop!"
    exit 1
}
Write-Host "ec2-autostop task -> shutdown.exe $armed"
Write-Host "this session stops in $($secs / 60) min"
