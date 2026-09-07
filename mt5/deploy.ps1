<#
.SYNOPSIS
    Deploy local MT5 EA source to the MetaTrader 5 terminal Experts folder and compile.

.DESCRIPTION
    This is the ONLY supported way to get code into MT5.
    Edit source under mt5\Experts\<Strategy>\ , then run this script.
    It copies each strategy folder's CONTENTS into the terminal's MQL5\Experts
    folder (so <Strategy>\*.mqh includes land correctly) and compiles each .mq5.

    NEVER edit files directly in the terminal Experts folder - they are overwritten
    on every deploy and are not under version control.

.PARAMETER Strategy
    Name of a single strategy folder to deploy (e.g. XauTickAccumulator).
    Default "all" deploys every strategy.

.PARAMETER NoCompile
    Copy files but skip the MetaEditor compile step.

.EXAMPLE
    .\deploy.ps1
    .\deploy.ps1 -Strategy XauTickAccumulator
    .\deploy.ps1 -Strategy GoldScalperMulti -NoCompile
#>
param(
    [string]$Strategy = "all",
    [switch]$NoCompile,
    [string]$TerminalId = "",     # deploy to one specific data folder (the hash)
    [switch]$AllTerminals,        # deploy to every data folder found
    [switch]$ListTerminals        # just show what is installed, deploy nothing
)

$ErrorActionPreference = "Stop"

# --- Which MT5 are we deploying INTO? -------------------------------------
#
# THIS MACHINE HAS MORE THAN ONE MT5 INSTALL, and each keeps its own data
# folder under %APPDATA%\MetaQuotes\Terminal\<hash>\. Hardcoding one hash
# silently deployed every strategy into a terminal the operator was not
# looking at - the EA compiled fine and simply never appeared in the
# Navigator. Resolve it instead, and always PRINT the choice.
#
# Auto-detect = the data folder whose logs were written most recently, which
# is the terminal that is actually running. Override with -TerminalId, or
# use -AllTerminals when several installs are in play.

function Get-Mt5Terminals {
    $base = Join-Path $env:APPDATA "MetaQuotes\Terminal"
    if (-not (Test-Path $base)) { throw "No MetaQuotes Terminal folder at $base" }
    Get-ChildItem $base -Directory |
        Where-Object { $_.Name -notin 'Common','Community' -and $_.Name.Length -ge 30 } |
        ForEach-Object {
            $origin = ""
            $originFile = Join-Path $_.FullName "origin.txt"
            if (Test-Path $originFile) { $origin = (Get-Content $originFile -Raw).Trim() }
            $lastLog = [datetime]::MinValue
            $logDir = Join-Path $_.FullName "logs"
            if (Test-Path $logDir) {
                $f = Get-ChildItem $logDir -Filter *.log -ErrorAction SilentlyContinue |
                     Sort-Object LastWriteTime -Descending | Select-Object -First 1
                if ($f) { $lastLog = $f.LastWriteTime }
            }
            $editor = ""
            if ($origin -and (Test-Path (Join-Path $origin "metaeditor64.exe"))) {
                $editor = Join-Path $origin "metaeditor64.exe"
            }
            [PSCustomObject]@{
                Id      = $_.Name
                Experts = Join-Path $_.FullName "MQL5\Experts"
                Origin  = $origin
                Editor  = $editor
                LastLog = $lastLog
            }
        } | Sort-Object LastLog -Descending
}

$all = @(Get-Mt5Terminals)
if ($all.Count -eq 0) { throw "No MT5 terminal data folders found under %APPDATA%\MetaQuotes\Terminal" }

if ($ListTerminals) {
    Write-Host "MT5 terminals on this machine (most recently active first):" -ForegroundColor Cyan
    $all | ForEach-Object {
        "  {0}  last active {1:yyyy-MM-dd HH:mm}  {2}" -f $_.Id.Substring(0,12), $_.LastLog, $_.Origin
    }
    return
}

if ($TerminalId) {
    $chosen = @($all | Where-Object { $_.Id -like "$TerminalId*" })
    if ($chosen.Count -eq 0) { throw "No terminal data folder matching '$TerminalId'. Try -ListTerminals." }
} elseif ($AllTerminals) {
    $chosen = $all
} else {
    $chosen = @($all[0])   # most recently active = the one that is running
}

Write-Host "Deploying into:" -ForegroundColor Cyan
$chosen | ForEach-Object {
    "  {0}  ({1})  last active {2:yyyy-MM-dd HH:mm}" -f $_.Id.Substring(0,12), $_.Origin, $_.LastLog
}
if (-not $TerminalId -and -not $AllTerminals -and $all.Count -gt 1) {
    Write-Host ("  note: {0} terminals installed. -ListTerminals to see them, -AllTerminals for all." -f $all.Count) -ForegroundColor DarkYellow
}

$SourceRoot = Join-Path $PSScriptRoot "Experts"
if (-not (Test-Path $SourceRoot)) { throw "Local source folder not found: $SourceRoot" }

# Resolve which strategies to deploy
if ($Strategy -eq "all") {
    $targets = Get-ChildItem -Path $SourceRoot -Directory | Select-Object -ExpandProperty Name
} else {
    if (-not (Test-Path (Join-Path $SourceRoot $Strategy))) { throw "Strategy folder not found: $Strategy" }
    $targets = @($Strategy)
}

foreach ($term in $chosen) {
    $TerminalExperts = $term.Experts
    if (-not (Test-Path $TerminalExperts)) {
        New-Item -ItemType Directory -Path $TerminalExperts -Force | Out-Null
    }
    # Compile with the MetaEditor belonging to THIS terminal's install. Using one
    # install's editor against another's folder is what produced an .ex5 that the
    # running terminal never saw.
    $MetaEditor = $term.Editor
    if (-not $MetaEditor) {
        # fall back to any metaeditor we can find, so a missing origin.txt is not fatal
        $MetaEditor = @(
            "C:\Program Files\MetaTrader 5\metaeditor64.exe",
            "C:\Program Files\MetaTrader 5 EXNESS\metaeditor64.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    }

    foreach ($name in $targets) {
        $srcDir = Join-Path $SourceRoot $name
        Write-Host ("==> Deploying {0} -> {1}" -f $name, $term.Id.Substring(0,12)) -ForegroundColor Cyan

        # Copy the strategy folder's contents into the terminal Experts root.
        # e.g. Experts\GoldBreakoutGrid\{GoldBreakoutGrid.mq5, GoldBreakoutGrid\*.mqh}
        #   -> MQL5\Experts\{GoldBreakoutGrid.mq5, GoldBreakoutGrid\*.mqh}
        Copy-Item -Path (Join-Path $srcDir "*") -Destination $TerminalExperts -Recurse -Force

        if (-not $NoCompile) {
            $deployedMq5 = Join-Path $TerminalExperts ("{0}.mq5" -f $name)
            if (-not $MetaEditor) {
                Write-Host "    (no metaeditor64.exe found - copied but NOT compiled)" -ForegroundColor Yellow
            } elseif (Test-Path $deployedMq5) {
                $log = Join-Path $env:TEMP ("compile_{0}.log" -f $name)
                & $MetaEditor /compile:"$deployedMq5" /log:"$log" | Out-Null
                if (Test-Path $log) {
                    $text = Get-Content -Path $log -Encoding Unicode -ErrorAction SilentlyContinue
                    $result = $text | Where-Object { $_ -match "Result:|error|warning" } | Select-Object -Last 1
                    if ($result) { Write-Host "    $($result.Trim())" -ForegroundColor Green }
                    Remove-Item $log -Force -ErrorAction SilentlyContinue
                }
                # Keep the compiled .ex5 next to its source, matching the other strategies.
                $builtEx5 = Join-Path $TerminalExperts ("{0}.ex5" -f $name)
                if (Test-Path $builtEx5) { Copy-Item $builtEx5 (Join-Path $srcDir ("{0}.ex5" -f $name)) -Force }
            } else {
                Write-Host "    (no top-level .mq5 named $name to compile)" -ForegroundColor Yellow
            }
        }
    }
}

Write-Host "Done. In MT5: enable Algo Trading and (re)attach the EA to a chart." -ForegroundColor Cyan

# metaeditor64.exe returns a non-zero exit code even on a clean compile; the
# per-strategy "Result:" line above is the real status. Exit clean.
exit 0
