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
    [switch]$NoCompile
)

$ErrorActionPreference = "Stop"

# --- Paths (edit these two if the terminal/install location changes) ---
$TerminalExperts = "C:\Users\Pandu\AppData\Roaming\MetaQuotes\Terminal\53785E099C927DB68A545C249CDBCE06\MQL5\Experts"
$MetaEditor      = "C:\Program Files\MetaTrader 5 EXNESS\metaeditor64.exe"

$SourceRoot = Join-Path $PSScriptRoot "Experts"

if (-not (Test-Path $TerminalExperts)) { throw "Terminal Experts folder not found: $TerminalExperts" }
if (-not (Test-Path $SourceRoot))      { throw "Local source folder not found: $SourceRoot" }

# Resolve which strategies to deploy
if ($Strategy -eq "all") {
    $targets = Get-ChildItem -Path $SourceRoot -Directory | Select-Object -ExpandProperty Name
} else {
    if (-not (Test-Path (Join-Path $SourceRoot $Strategy))) { throw "Strategy folder not found: $Strategy" }
    $targets = @($Strategy)
}

foreach ($name in $targets) {
    $srcDir = Join-Path $SourceRoot $name
    Write-Host "==> Deploying $name" -ForegroundColor Cyan

    # Copy the strategy folder's contents into the terminal Experts root.
    # e.g. Experts\GoldBreakoutGrid\{GoldBreakoutGrid.mq5, GoldBreakoutGrid\*.mqh}
    #   -> MQL5\Experts\{GoldBreakoutGrid.mq5, GoldBreakoutGrid\*.mqh}
    Copy-Item -Path (Join-Path $srcDir "*") -Destination $TerminalExperts -Recurse -Force

    if (-not $NoCompile) {
        $deployedMq5 = Join-Path $TerminalExperts ("{0}.mq5" -f $name)
        if (Test-Path $deployedMq5) {
            $log = Join-Path $env:TEMP ("compile_{0}.log" -f $name)
            & $MetaEditor /compile:"$deployedMq5" /log:"$log" | Out-Null
            if (Test-Path $log) {
                $text = Get-Content -Path $log -Encoding Unicode -ErrorAction SilentlyContinue
                $result = $text | Where-Object { $_ -match "Result:|error|warning" } | Select-Object -Last 1
                if ($result) { Write-Host "    $($result.Trim())" -ForegroundColor Green }
                Remove-Item $log -Force -ErrorAction SilentlyContinue
            }
        } else {
            Write-Host "    (no top-level .mq5 named $name to compile)" -ForegroundColor Yellow
        }
    }
}

Write-Host "Done. In MT5: enable Algo Trading and (re)attach the EA to a chart." -ForegroundColor Cyan

# metaeditor64.exe returns a non-zero exit code even on a clean compile; the
# per-strategy "Result:" line above is the real status. Exit clean.
exit 0
