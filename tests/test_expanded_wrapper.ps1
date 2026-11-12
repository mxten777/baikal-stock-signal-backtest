$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$wrapper = Join-Path $root "run_expanded_with_log.ps1"
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($wrapper, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$checks = 0
function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (!$Condition) { throw $Message }
    $script:checks++
}
$text = Get-Content -LiteralPath $wrapper -Raw
Assert-True ($text -match '\$root = \$PSScriptRoot') "Wrapper root must follow its repository"
Assert-True ($text -notmatch '[A-Za-z]:\\') "Wrapper contains an absolute drive path"
$commands = $ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.CommandAst] -and
    $node.InvocationOperator -eq [System.Management.Automation.Language.TokenKind]::Ampersand
}, $true)
Assert-True ($commands.Count -eq 1) "Expected exactly one executable invocation"
Assert-True ($commands[0].CommandElements[0].Extent.Text -eq '"$root\.venv\Scripts\python.exe"') "Unexpected executable"
Assert-True (($commands[0].CommandElements | Select-Object -Skip 1 | ForEach-Object { $_.Extent.Text }) -join ' ' -eq
    '-m scripts.expanded_operational_run --json --run-context scheduled') "Operational arguments changed"
Assert-True ($commands[0].Redirections[0].Extent.Text -eq '*> $log') "All output must reach the log"
Assert-True ($text -match 'exit \$LASTEXITCODE') "Native exit code must be forwarded"

$sandbox = Join-Path ([IO.Path]::GetTempPath()) ("expanded-wrapper-" + [guid]::NewGuid().ToString("N"))
try {
    foreach ($code in 0, 23) {
        $fixtureRoot = Join-Path $sandbox "repository with spaces $code"
        $bin = Join-Path $fixtureRoot ".venv\Scripts"
        New-Item -ItemType Directory -Path $bin -Force | Out-Null
        $fixtureWrapper = Join-Path $fixtureRoot "run_expanded_with_log.ps1"
        $text.Replace('python.exe', 'synthetic-python.ps1') | Set-Content $fixtureWrapper
        @(
            '$ErrorActionPreference = "Continue"'
            '"SYNTHETIC_ARGS=" + ($args -join "|")'
            '"SYNTHETIC_CWD=" + (Get-Location).Path'
            'Write-Error "SYNTHETIC_STDERR"'
            ('$global:LASTEXITCODE = ' + $code)
        ) | Set-Content (Join-Path $bin "synthetic-python.ps1")
        $shell = (Get-Process -Id $PID).Path
        & $shell -NoProfile -File $fixtureWrapper
        Assert-True ($LASTEXITCODE -eq $code) "Exit code was not forwarded"
        $logs = @(Get-ChildItem (Join-Path $fixtureRoot "output\expanded_scheduler_logs") -File)
        Assert-True ($logs.Count -eq 1 -and $logs[0].Name -match '^expanded_\d{8}_\d{6}\.log$') "Timestamped log missing"
        $logged = Get-Content $logs[0].FullName -Raw
        Assert-True ($logged.Contains('SYNTHETIC_ARGS=-m|scripts.expanded_operational_run|--json|--run-context|scheduled')) "Arguments changed"
        Assert-True ($logged.Contains("SYNTHETIC_CWD=$fixtureRoot")) "Working directory is not the wrapper root"
        Assert-True ($logged.Contains("SYNTHETIC_STDERR")) "Stderr was not logged"
    }
    $missingRoot = Join-Path $sandbox "missing interpreter"
    New-Item -ItemType Directory -Path $missingRoot | Out-Null
    Copy-Item -LiteralPath $wrapper -Destination (Join-Path $missingRoot "run_expanded_with_log.ps1")
    $ErrorActionPreference = "Continue"
    $failure = & $shell -NoProfile -File (Join-Path $missingRoot "run_expanded_with_log.ps1") 2>&1
    $failureExitCode = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    Assert-True ($failureExitCode -ne 0) "Missing interpreter must fail"
    Assert-True (!!$failure) "Missing interpreter must report an error"
}
finally {
    if (Test-Path -LiteralPath $sandbox) { Remove-Item -LiteralPath $sandbox -Recurse -Force }
}
Write-Host "PASS: $checks wrapper checks; synthetic command only, no analysis or Scheduler changes."
