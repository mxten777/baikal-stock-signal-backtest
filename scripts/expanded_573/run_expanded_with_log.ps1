param(
    [string]$SourceDate,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$python = 'C:\baikal777\baikal-stock-signal-backtest\.venv\Scripts\python.exe'
$entry = Join-Path $root 'run_expanded_573.py'
$logDir = Join-Path $root 'output\expanded_scheduler_logs'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
$runId = "$stamp-$PID"
$log = Join-Path $logDir "expanded_$runId.log"
$encoding = New-Object System.Text.UTF8Encoding($false)
$exitCode = 1
$failureStage = 'INITIALIZATION'
$failureReason = ''
$targetDate = if ($SourceDate) { $SourceDate } else { (Get-Date -Format 'yyyy-MM-dd') }

function Write-RunLog {
    param([string]$Message)
    [System.IO.File]::AppendAllText($script:log, $Message + [Environment]::NewLine, $script:encoding)
}

try {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    Write-RunLog "RUN_START run_id=$runId started_at=$((Get-Date).ToString('o')) source_date=$targetDate"

    $failureStage = 'CONFIGURATION'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or
        -not (Test-Path -LiteralPath $entry -PathType Leaf)) {
        throw 'Expanded 573 interpreter or entry point missing'
    }

    $arguments = @('-I', '-B', $entry)
    if ($SourceDate) { $arguments += @('--source-date', $SourceDate) }
    if ($CheckOnly) { $arguments += '--check' }
    Set-Location -LiteralPath $root

    $failureStage = 'PYTHON'
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $python @arguments 2>&1 | ForEach-Object {
            Write-RunLog ([string]$_)
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($null -eq $exitCode) { $exitCode = 1 }
    if ($exitCode -ne 0) { $failureReason = "Python exit code $exitCode" }
} catch {
    $exitCode = 1
    $failureReason = $_.Exception.Message -replace '[\r\n]+', ' '
    if (Test-Path -LiteralPath $log) {
        Write-RunLog "RUN_ERROR stage=$failureStage reason=$failureReason"
    }
} finally {
    if (Test-Path -LiteralPath $log) {
        if (-not $failureReason) { $failureReason = '-' }
        Write-RunLog "RUN_END run_id=$runId ended_at=$((Get-Date).ToString('o')) exit_code=$exitCode failure_stage=$failureStage failure_reason=$failureReason"
    }
}

exit $exitCode
