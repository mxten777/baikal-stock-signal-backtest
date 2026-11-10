$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$tokens = $null
$errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $root "scripts\run_mobile_dashboard.ps1"), [ref]$tokens, [ref]$errors
)
if ($errors.Count) {
    throw ($errors | Out-String)
}
foreach ($node in $ast.EndBlock.Statements) {
    if ($node -is [System.Management.Automation.Language.FunctionDefinitionAst]) {
        . ([scriptblock]::Create($node.Extent.Text))
    }
}

$checks = 0
function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
    $script:checks++
}
function Assert-Stops {
    param([scriptblock]$Action, [string]$Message)
    try { & $Action }
    catch {
        Assert-True ($_.Exception.Message -like "STOP: *$Message*") $_.Exception.Message
        return
    }
    throw "Expected STOP: $Message"
}

$LocalDashboard = $true
$frontendPort = 5173
$frontend = Join-Path $root "dashboard\frontend"
$python = Join-Path $root ".venv\Scripts\python.exe"
$vite = Join-Path $frontend "node_modules\vite\bin\vite.js"
$script:listeners = @()
function Get-PortListeners { param([int]$Port) return $script:listeners }
function Get-ListenerProcess { param([int]$ProcessId) return $script:process }
function Get-CimInstance { return $script:parent }

Assert-True ($null -eq (Get-VerifiedFrontendBind -Frontend $frontend)) "Empty port must be available"
$script:listeners = @([pscustomobject]@{ OwningProcess = 123; LocalAddress = "127.0.0.1" })
$script:process = [pscustomobject]@{
    ExecutablePath = "C:\Program Files\nodejs\node.exe"
    CommandLine = 'node "' + $vite + '"'
}
Assert-True ((Get-VerifiedFrontendBind -Frontend $frontend) -eq "127.0.0.1") "Valid PC Vite rejected"
$script:process.CommandLine = 'node "C:\other-project\node_modules\vite\bin\vite.js"'
Assert-Stops { Get-VerifiedFrontendBind -Frontend $frontend } "unknown process"
$script:process.CommandLine = 'node "' + $vite + '"'
$script:listeners[0].LocalAddress = "0.0.0.0"
Assert-Stops { Get-VerifiedFrontendBind -Frontend $frontend } "127.0.0.1 only"
$LocalDashboard = $false
$frontendPort = 5174
Assert-True ((Get-VerifiedFrontendBind -Frontend $frontend) -eq "0.0.0.0") "Mobile LAN bind regressed"
$script:listeners[0].LocalAddress = "127.0.0.1"
Assert-Stops { Get-VerifiedFrontendBind -Frontend $frontend } "localhost-only"
$LocalDashboard = $true
$frontendPort = 5173
$script:process = [pscustomobject]@{
    ExecutablePath = $python
    CommandLine = '"python.exe" -B -m dashboard.api'
    ParentProcessId = 456
}
Assert-True (Test-BackendProcess -ProcessId 123 -PythonPath $python) "Valid backend rejected"
$script:process.ExecutablePath = "C:\base-python\python.exe"
$script:parent = [pscustomobject]@{ ExecutablePath = $python; CommandLine = '"python.exe" -B -m dashboard.api' }
Assert-True (Test-BackendProcess -ProcessId 123 -PythonPath $python) "Venv child backend rejected"
$script:parent.ExecutablePath = "C:\other-project\.venv\Scripts\python.exe"
Assert-Stops { Assert-BackendListener -PythonPath $python } "unknown process"
$script:listeners[0].LocalAddress = "0.0.0.0"
Assert-Stops { Assert-BackendListener -PythonPath $python } "unexpected listener"

$savedRoot = $env:BAIKAL_EXPANDED_PARTIAL_ROOT
try {
    $env:BAIKAL_EXPANDED_PARTIAL_ROOT = $root
    Initialize-PartialEnvironment
    Assert-True ($env:BAIKAL_EXPANDED_PARTIAL_ROOT -eq $root) "Explicit root was overridden"
    $env:BAIKAL_EXPANDED_PARTIAL_ROOT = "relative\not-approved"
    Assert-Stops { Initialize-PartialEnvironment } "existing verified absolute root"
}
finally {
    $env:BAIKAL_EXPANDED_PARTIAL_ROOT = $savedRoot
}

$script:response = [pscustomobject]@{ StatusCode = 200; Content = '{"read_only":true,"status":"PARTIAL","items":[]}' }
function Invoke-WebRequest { return $script:response }
Assert-PartialApi -BaseUrl "http://test"
$script:response.Content = '{"read_only":false,"status":"READY","items":[]}'
Assert-Stops { Assert-PartialApi -BaseUrl "http://test" } "unexpected response"
function Invoke-WebRequest { throw "503 Service Unavailable" }
Assert-Stops { Assert-PartialApi -BaseUrl "http://test" } "503 Service Unavailable"

function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle)
    $script:started = $ArgumentList
}
Start-FrontendWindow -Frontend $frontend -NpmPath "C:\Program Files\nodejs\npm.cmd"
Assert-True ($script:started -like '*--host 127.0.0.1 --port 5173 --strictPort') "PC frontend must use strict loopback port"
$LocalDashboard = $false
$frontendPort = 5174
Start-FrontendWindow -Frontend $frontend -NpmPath "C:\Program Files\nodejs\npm.cmd"
Assert-True ($script:started -like '*--host 0.0.0.0 --port 5174 --strictPort') "Mobile frontend arguments changed"
Start-BackendWindow -Root $root -Python $python
Assert-True ($script:started -like '* -B -m dashboard.api') "Backend must only start the existing API"
Write-Host "PASS: $checks launcher checks; no servers, data or user settings changed."
