$root = $PSScriptRoot
Set-Location $root -ErrorAction Stop

$logDir = Join-Path $root "output\expanded_scheduler_logs"
New-Item -ItemType Directory -Force -Path $logDir -ErrorAction Stop | Out-Null
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$log = Join-Path $logDir "expanded_$stamp.log"

if (-not (Test-Path -LiteralPath "$root\.venv\Scripts\python.exe" -PathType Leaf)) {
    throw "Repository Python interpreter not found."
}

& "$root\.venv\Scripts\python.exe" `
    -m scripts.expanded_operational_run `
    --json --run-context scheduled `
    *> $log

exit $LASTEXITCODE
