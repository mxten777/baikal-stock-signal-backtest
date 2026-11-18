$ErrorActionPreference = 'Stop'
$repository = Split-Path -Parent $PSScriptRoot
$wrapper = Join-Path $repository 'scripts\expanded_573\run_expanded_with_log.ps1'
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($wrapper, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
if ((Get-Content -LiteralPath $wrapper -Raw) -notmatch 'RUN_START' -or
    (Get-Content -LiteralPath $wrapper -Raw) -notmatch 'RUN_END') {
    throw 'Scheduled wrapper must record start and end metadata.'
}

$sandbox = Join-Path ([IO.Path]::GetTempPath()) ("expanded-573-wrapper-" + [guid]::NewGuid().ToString('N'))
$checks = 0
function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
    $script:checks++
}

try {
    $shell = (Get-Process -Id $PID).Path
    foreach ($code in 0, 23) {
        $fixtureRoot = Join-Path $sandbox "run $code"
        $bin = Join-Path $fixtureRoot '.venv\Scripts'
        New-Item -ItemType Directory -Path $bin -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $fixtureRoot 'run_expanded_573.py') -Value ''
        $fixtureWrapper = Join-Path $fixtureRoot 'run_expanded_with_log.ps1'
        $fixturePython = Join-Path $bin 'synthetic-python.ps1'
        (Get-Content -LiteralPath $wrapper -Raw).Replace(
            'C:\baikal777\baikal-stock-signal-backtest\.venv\Scripts\python.exe',
            $fixturePython
        ) | Set-Content -LiteralPath $fixtureWrapper
        @(
            '$ErrorActionPreference = "Continue"'
            '"SYNTHETIC_ARGS=" + ($args -join "|")'
            '"SYNTHETIC_CWD=" + (Get-Location).Path'
            'Write-Error "SYNTHETIC_STDERR"'
            ('$global:LASTEXITCODE = ' + $code)
        ) | Set-Content -LiteralPath $fixturePython

        & $shell -NoProfile -File $fixtureWrapper
        Assert-True ($LASTEXITCODE -eq $code) "Exit code $code was not forwarded."
        $logs = @(Get-ChildItem (Join-Path $fixtureRoot 'output\expanded_scheduler_logs') -File)
        Assert-True ($logs.Count -eq 1) 'Expected one per-run log.'
        $logged = Get-Content -LiteralPath $logs[0].FullName -Raw
        Assert-True ($logged -match 'RUN_START .*started_at=') 'Start metadata missing.'
        Assert-True ($logged -match "RUN_END .*exit_code=$code") 'End status missing.'
        Assert-True ($logged.Contains('SYNTHETIC_STDERR')) 'Child stderr was not logged.'
        Assert-True ($logged.Contains("SYNTHETIC_CWD=$fixtureRoot")) 'Child working directory changed.'
    }

    $missingRoot = Join-Path $sandbox 'missing interpreter'
    New-Item -ItemType Directory -Path $missingRoot -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $missingRoot 'run_expanded_573.py') -Value ''
    (Get-Content -LiteralPath $wrapper -Raw).Replace(
        'C:\baikal777\baikal-stock-signal-backtest\.venv\Scripts\python.exe',
        (Join-Path $missingRoot '.venv\Scripts\python.exe')
    ) | Set-Content -LiteralPath (Join-Path $missingRoot 'run_expanded_with_log.ps1')
    & $shell -NoProfile -File (Join-Path $missingRoot 'run_expanded_with_log.ps1')
    Assert-True ($LASTEXITCODE -eq 1) 'Missing interpreter must fail.'
    $failureLog = Get-ChildItem (Join-Path $missingRoot 'output\expanded_scheduler_logs') -File |
        Get-Content -Raw
    Assert-True ($failureLog -match 'RUN_ERROR stage=CONFIGURATION') 'Configuration failure stage missing.'
    Assert-True ($failureLog -match 'RUN_END .*exit_code=1') 'Failure exit status missing.'
} finally {
    if (Test-Path -LiteralPath $sandbox) {
        Remove-Item -LiteralPath $sandbox -Recurse -Force
    }
}

Write-Host "PASS: $checks scheduled wrapper logging checks; synthetic child only."
