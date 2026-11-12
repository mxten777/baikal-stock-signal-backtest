param(
    [string]$SourceDate,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = 'C:\baikal777\baikal-stock-signal-backtest\.venv\Scripts\python.exe'
$entry = Join-Path $root 'run_expanded_573.py'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $entry)) {
    throw 'Expanded 573 interpreter or entry point missing'
}
$arguments = @('-I', '-B', $entry)
if ($SourceDate) { $arguments += @('--source-date', $SourceDate) }
if ($CheckOnly) {
    $arguments += '--check'
    & $python @arguments
    exit $LASTEXITCODE
}
$logDir = Join-Path $root 'output\expanded_scheduler_logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$log = Join-Path $logDir ("expanded_{0}_{1}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss_fff'), $PID)
Set-Location -LiteralPath $root
& $python @arguments *> $log
exit $LASTEXITCODE
