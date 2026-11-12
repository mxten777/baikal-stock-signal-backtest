param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot,
    [switch]$LocalDashboard
)

$ErrorActionPreference = "Stop"
$frontendPort = 5174
if ($LocalDashboard) {
    $frontendPort = 5173
}
$serviceLock = $null
$lockHeld = $false

function Stop-MobileLauncher {
    param([string]$Message)
    throw "STOP: $Message"
}

function Get-PortListeners {
    param([int]$Port)
    try {
        return @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
            Where-Object { $_.LocalPort -eq $Port })
    }
    catch {
        Stop-MobileLauncher "Could not inspect TCP port ${Port}: $($_.Exception.Message)"
    }
}

function Get-ListenerProcess {
    param([int]$ProcessId)
    $process = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Stop-MobileLauncher "Cannot identify the process listening on the required port (PID $ProcessId)."
    }
    return $process
}

function Test-BackendProcess {
    param(
        [int]$ProcessId,
        [string]$PythonPath
    )

    $process = Get-ListenerProcess -ProcessId $ProcessId
    $modulePattern = '(?i)(^|\s)-m\s+dashboard\.api(\s|$)'
    if ([string]::Equals([string]$process.ExecutablePath, $PythonPath, [StringComparison]::OrdinalIgnoreCase) -and
        [string]$process.CommandLine -match $modulePattern) {
        return $true
    }

    if ([string]$process.CommandLine -notmatch $modulePattern -or $process.ParentProcessId -le 0) {
        return $false
    }

    $parent = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $($process.ParentProcessId)" -ErrorAction SilentlyContinue
    return $null -ne $parent -and
        [string]::Equals([string]$parent.ExecutablePath, $PythonPath, [StringComparison]::OrdinalIgnoreCase) -and
        [string]$parent.CommandLine -match $modulePattern
}

function Test-HttpJson {
    param(
        [string]$Url,
        [int]$TimeoutSec = 3,
        [scriptblock]$Validate
    )
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
        if ($response.StatusCode -ne 200) {
            return $null
        }
        $json = $response.Content | ConvertFrom-Json -ErrorAction Stop
        if (& $Validate $json) {
            return $json
        }
    }
    catch {
        return $null
    }
    return $null
}

function Get-LanAddress {
    $excludedAdapter = '(?i)(virtual|hyper[\s-]?v|wsl|vmware|virtualbox|vbox|vpn|tap[- ]|tun[- ]|wireguard|tailscale|zerotier|docker|container|loopback|vethernet)'
    $physicalLanAdapter = '(?i)(wi-?fi|wireless|802\.11|ethernet|gigabit|gbe|\blan\b)'
    $candidateRows = @()

    try {
        $adapters = @(Get-NetAdapter -Physical -ErrorAction Stop | Where-Object {
            $_.Status -eq "Up" -and
            $_.Name -notmatch $excludedAdapter -and
            $_.InterfaceDescription -notmatch $excludedAdapter -and
            ($_.Name -match $physicalLanAdapter -or $_.InterfaceDescription -match $physicalLanAdapter)
        })
    }
    catch {
        Stop-MobileLauncher "Could not inspect physical network adapters: $($_.Exception.Message)"
    }

    foreach ($adapter in $adapters) {
        $configuration = Get-NetIPConfiguration -InterfaceIndex $adapter.ifIndex -ErrorAction Stop
        $gateways = @($configuration.IPv4DefaultGateway | Where-Object { $_.NextHop -and $_.NextHop -ne "0.0.0.0" })
        if ($gateways.Count -eq 0) {
            continue
        }

        $routes = @(Get-NetRoute -DestinationPrefix "0.0.0.0/0" -InterfaceIndex $adapter.ifIndex -ErrorAction Stop |
            Where-Object { $_.NextHop -in @($gateways | ForEach-Object { $_.NextHop }) })
        if ($routes.Count -eq 0) {
            continue
        }
        $routeMetric = ($routes | Measure-Object -Property RouteMetric -Minimum).Minimum
        $interfaceSettings = @(Get-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction Stop)
        if ($interfaceSettings.Count -eq 0) {
            continue
        }
        $interfaceMetric = ($interfaceSettings | Measure-Object -Property InterfaceMetric -Minimum).Minimum
        $totalMetric = [int]$routeMetric + [int]$interfaceMetric

        $addresses = @(Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction Stop |
            Where-Object {
                $_.AddressState -eq "Preferred" -and
                $_.IPAddress -ne "127.0.0.1" -and
                $_.IPAddress -notlike "127.*" -and
                $_.IPAddress -notlike "169.254.*" -and
                $_.IPAddress -ne "0.0.0.0"
            })
        foreach ($address in $addresses) {
            $candidateRows += [pscustomobject]@{
                Name = $adapter.Name
                Description = $adapter.InterfaceDescription
                IPAddress = $address.IPAddress
                Metric = $totalMetric
                Gateway = ($gateways | Select-Object -First 1 -ExpandProperty NextHop)
            }
        }
    }

    if ($candidateRows.Count -eq 0) {
        Write-Host "[WARN] No active physical Wi-Fi/Ethernet IPv4 with a default gateway was found."
        return $null
    }

    $bestMetric = ($candidateRows | Measure-Object -Property Metric -Minimum).Minimum
    $bestCandidates = @($candidateRows | Where-Object { $_.Metric -eq $bestMetric } |
        Sort-Object Name, IPAddress -Unique)
    if ($bestCandidates.Count -ne 1) {
        Write-Host "[WARN] LAN IPv4 is ambiguous; no address was selected. Candidate adapters:"
        foreach ($candidate in $candidateRows | Sort-Object Metric, Name, IPAddress -Unique) {
            Write-Host ("       {0} ({1})  {2}  gateway={3} metric={4}" -f $candidate.Name, $candidate.Description, $candidate.IPAddress, $candidate.Gateway, $candidate.Metric)
        }
        return $null
    }

    return $bestCandidates[0]
}

function Start-BackendWindow {
    param([string]$Root, [string]$Python)
    $command = '/k cd /d "' + $Root + '" && "' + $Python + '" -B -m dashboard.api'
    Start-Process -FilePath $env:ComSpec -ArgumentList $command -WorkingDirectory $Root -WindowStyle Normal | Out-Null
}

function Start-FrontendWindow {
    param([string]$Frontend, [string]$NpmPath)
    $bindHost = "0.0.0.0"
    if ($LocalDashboard) {
        $bindHost = "127.0.0.1"
    }
    $command = '/k cd /d "' + $Frontend + '" && "' + $NpmPath + '" run dev -- --host ' + $bindHost + ' --port ' + $frontendPort + ' --strictPort'
    Start-Process -FilePath $env:ComSpec -ArgumentList $command -WorkingDirectory $Frontend -WindowStyle Normal | Out-Null
}

function Assert-BackendListener {
    param([string]$PythonPath)
    $listeners = @(Get-PortListeners -Port 8765)
    if ($listeners.Count -gt 0) {
        $processIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
        $addresses = @($listeners | Select-Object -ExpandProperty LocalAddress -Unique)
        if ($processIds.Count -ne 1 -or ($addresses | Where-Object { $_ -ne "127.0.0.1" }).Count -gt 0) {
            Stop-MobileLauncher "Port 8765 has an unexpected listener or bind address."
        }
        if (-not (Test-BackendProcess -ProcessId $processIds[0] -PythonPath $PythonPath)) {
            Stop-MobileLauncher "Port 8765 is already in use by an unknown process. No process was stopped."
        }
    }
}

function Wait-ForBackend {
    param([string]$PythonPath)
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        $listeners = @(Get-PortListeners -Port 8765)
        if ($listeners.Count -gt 0) {
            Assert-BackendListener -PythonPath $PythonPath
            $health = Test-HttpJson -Url "http://127.0.0.1:8765/api/dashboard/expanded-shadow" -TimeoutSec 20 -Validate {
                param($payload)
                return $payload.mode -eq "EXPANDED_SHADOW" -and $payload.read_only -eq $true
            }
            if ($null -ne $health) {
                return "RUNNING"
            }
        }
        Start-Sleep -Seconds 1
    }
    Stop-MobileLauncher "Backend did not become ready on 127.0.0.1:8765 within 30 bounded attempts."
}

function Get-VerifiedFrontendBind {
    param(
        [string]$Frontend,
        [string]$LanIP
    )
    $viteEntry = [IO.Path]::GetFullPath((Join-Path $Frontend "node_modules\vite\bin\vite.js"))
    $listeners = @(Get-PortListeners -Port $frontendPort)
    if ($listeners.Count -eq 0) {
        return
    }
    $processIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($processIds.Count -ne 1) {
        Stop-MobileLauncher "Port $frontendPort has multiple listener processes; refusing to guess."
    }
    $process = Get-ListenerProcess -ProcessId $processIds[0]
    $commandLine = [string]$process.CommandLine
    $actualPath = [string]$process.ExecutablePath
    $viteMatch = [regex]::Match($commandLine, '(?i)(?<entry>[A-Z]:\\[^"]*?vite[\\/]+bin[\\/]+vite\.js)')
    $entryMatchesRepository = $false
    if ($viteMatch.Success) {
        try {
            $actualViteEntry = [IO.Path]::GetFullPath($viteMatch.Groups["entry"].Value)
            $entryMatchesRepository = [string]::Equals(
                $actualViteEntry, $viteEntry, [StringComparison]::OrdinalIgnoreCase
            )
        }
        catch {
            Stop-MobileLauncher "Cannot resolve frontend entry path: $($_.Exception.Message)"
        }
    }
    if ([IO.Path]::GetFileName($actualPath) -ine "node.exe" -or -not $entryMatchesRepository) {
        Stop-MobileLauncher "Port $frontendPort is already in use by an unknown process. No process was stopped."
    }

    $addresses = @($listeners | Select-Object -ExpandProperty LocalAddress -Unique)
    if ($LocalDashboard) {
        if ($addresses.Count -ne 1 -or $addresses[0] -ne "127.0.0.1") {
            Stop-MobileLauncher "PC frontend port $frontendPort must be bound to 127.0.0.1 only."
        }
        return $addresses[0]
    }
    if ($addresses -contains "127.0.0.1" -and
        ($addresses | Where-Object { $_ -ne "127.0.0.1" }).Count -eq 0) {
        Stop-MobileLauncher "Existing frontend is localhost-only.`nClose the existing frontend window and run`nrun_mobile_dashboard.bat again."
    }
    $validLanBind = $addresses.Count -eq 1 -and $addresses[0] -eq "0.0.0.0"
    if ($addresses.Count -eq 1 -and -not [string]::IsNullOrWhiteSpace($LanIP)) {
        $validLanBind = $validLanBind -or $addresses[0] -eq $LanIP
    }
    elseif ($addresses.Count -eq 1) {
        $bindAddress = $null
        if ([System.Net.IPAddress]::TryParse([string]$addresses[0], [ref]$bindAddress) -and
            $bindAddress.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
            -not $bindAddress.Equals([System.Net.IPAddress]::Any) -and
            -not [System.Net.IPAddress]::IsLoopback($bindAddress) -and
            -not $bindAddress.ToString().StartsWith("169.254.")) {
            $validLanBind = $true
        }
    }
    if (-not $validLanBind) {
        Stop-MobileLauncher "Port $frontendPort is not verifiably bound for IPv4 LAN access (IPv6-only or unknown bind)."
    }
    return $addresses[0]
}

function Wait-ForFrontend {
    param([string]$Frontend, [string]$LanIP)
    $apiAttempts = 0
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        $bind = Get-VerifiedFrontendBind -Frontend $Frontend -LanIP $LanIP
        if ($null -ne $bind) {
            $page = $null
            try {
                $page = Invoke-WebRequest -Uri $pcUrl -UseBasicParsing -TimeoutSec 5
            }
            catch {
                $page = $null
            }
            if ($null -ne $page -and $page.StatusCode -eq 200 -and $page.Content -match '(?i)<html') {
                $apiAttempts++
                $api = Test-HttpJson -Url "http://127.0.0.1:${frontendPort}/api/dashboard/expanded-shadow" -TimeoutSec 20 -Validate {
                    param($payload)
                    return $payload -is [System.Management.Automation.PSCustomObject] -and
                        $payload.mode -eq "EXPANDED_SHADOW" -and
                        $null -ne $payload.PSObject.Properties["status"] -and
                        $null -ne $payload.PSObject.Properties["run_summary"]
                }
                if ($null -ne $api) {
                    return [pscustomobject]@{
                        Api = $api
                        BindAddress = $bind
                    }
                }
                if ($apiAttempts -ge 3) {
                    Stop-MobileLauncher "Vite proxy did not return the expected Expanded Shadow JSON after 3 bounded attempts."
                }
            }
        }
        Start-Sleep -Seconds 1
    }
    Stop-MobileLauncher "Frontend route or proxied Expanded Shadow API did not become ready within the bounded retry limits."
}

function Initialize-PartialEnvironment {
    if ([string]::IsNullOrWhiteSpace($env:BAIKAL_EXPANDED_PARTIAL_ROOT)) {
        $env:BAIKAL_EXPANDED_PARTIAL_ROOT = [Environment]::GetEnvironmentVariable("BAIKAL_EXPANDED_PARTIAL_ROOT", "User")
    }
    if ([string]::IsNullOrWhiteSpace($env:BAIKAL_EXPANDED_PARTIAL_ROOT)) {
        Stop-MobileLauncher "BAIKAL_EXPANDED_PARTIAL_ROOT is not configured. Save the verified existing isolated root in the Windows User environment; see docs\DAILY_OPERATIONS_MANUAL.md. Do not run analysis."
    }
    if (-not [IO.Path]::IsPathRooted($env:BAIKAL_EXPANDED_PARTIAL_ROOT) -or
        -not (Test-Path -LiteralPath $env:BAIKAL_EXPANDED_PARTIAL_ROOT -PathType Container)) {
        Stop-MobileLauncher "BAIKAL_EXPANDED_PARTIAL_ROOT must name an existing verified absolute root. No directory was created."
    }
}

function Initialize-ExpandedEnvironment {
    if ([string]::IsNullOrWhiteSpace($env:BAIKAL_EXPANDED_DATA_ROOT)) {
        $env:BAIKAL_EXPANDED_DATA_ROOT = [Environment]::GetEnvironmentVariable("BAIKAL_EXPANDED_DATA_ROOT", "User")
    }
    if ([string]::IsNullOrWhiteSpace($env:BAIKAL_EXPANDED_DATA_ROOT)) {
        return
    }
    if (-not [IO.Path]::IsPathRooted($env:BAIKAL_EXPANDED_DATA_ROOT) -or
        -not (Test-Path -LiteralPath $env:BAIKAL_EXPANDED_DATA_ROOT -PathType Container)) {
        Stop-MobileLauncher "BAIKAL_EXPANDED_DATA_ROOT must name an existing verified absolute publication root. No directory was created."
    }
    Write-Host "[INFO] Expanded publication root: $env:BAIKAL_EXPANDED_DATA_ROOT"
}

function Assert-ExpandedDataApi {
    param([string]$BaseUrl)
    if ([string]::IsNullOrWhiteSpace($env:BAIKAL_EXPANDED_DATA_ROOT)) {
        return
    }
    $source = Join-Path $env:BAIKAL_EXPANDED_DATA_ROOT "output\expanded_shadow\expanded_shadow_run.json"
    try {
        $manifest = Get-Content -LiteralPath $source -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        $response = Invoke-WebRequest -Uri "$BaseUrl/api/dashboard/expanded-shadow" -UseBasicParsing -TimeoutSec 20
        $board = $response.Content | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        Stop-MobileLauncher "Expanded publication check failed at ${BaseUrl}: $($_.Exception.Message)"
    }
    if ($response.StatusCode -ne 200 -or $board.read_only -ne $true -or
        $board.mode -ne "EXPANDED_SHADOW" -or
        [string]::IsNullOrWhiteSpace($manifest.run_id) -or
        $board.run_summary.run_id -cne $manifest.run_id -or
        -not [string]::Equals([string]$board.run_summary.source, $source, [StringComparison]::OrdinalIgnoreCase)) {
        Stop-MobileLauncher "Expanded API does not match BAIKAL_EXPANDED_DATA_ROOT at ${BaseUrl}. Restart only the identified Dashboard API with the saved publication root. No process was stopped."
    }
    Write-Host ("[EXPANDED] {0} {1} {2}/{3} signals={4} candidates={5}" -f
        $board.run_summary.source_date, $board.status, $board.run_summary.ready,
        $board.run_summary.universe, $board.run_summary.signals, $board.run_summary.candidate)
}

function Assert-PartialApi {
    param([string]$BaseUrl)
    try {
        $response = Invoke-WebRequest -Uri "$BaseUrl/api/dashboard/expanded-shadow/partial" -UseBasicParsing -TimeoutSec 20
        $partial = $response.Content | ConvertFrom-Json -ErrorAction Stop
    }
    catch {
        Stop-MobileLauncher "PARTIAL API failed at ${BaseUrl}: $($_.Exception.Message) $($_.ErrorDetails.Message). If an existing API has old environment settings, restart only that identified API using the manual. No process was stopped."
    }
    if ($response.StatusCode -ne 200 -or $partial.read_only -ne $true -or
        $partial.status -notin @("PARTIAL", "MISSING") -or
        $null -eq $partial.PSObject.Properties["items"]) {
        Stop-MobileLauncher "PARTIAL API returned an unexpected response at $BaseUrl."
    }
    if ($partial.status -eq "MISSING") {
        Write-Host "[WARN] PARTIAL API is ready but the configured root has no saved PARTIAL results."
    }
    foreach ($run in $partial.items) {
        Write-Host ("[PARTIAL] {0} {1}/{2} signals={3} candidates={4}" -f $run.source_date, $run.ready, $run.universe, $run.signals, $run.candidates)
    }
}

try {
    $root = [IO.Path]::GetFullPath($RepositoryRoot.TrimEnd("\"))
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw "ERROR: Repository root was not found: $root"
    }

    $python = Join-Path $root ".venv\Scripts\python.exe"
    $frontend = Join-Path $root "dashboard\frontend"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "ERROR: venv Python not found: $python"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $frontend "package.json") -PathType Leaf)) {
        throw "ERROR: Frontend package.json not found under $frontend"
    }
    $npm = Get-Command "npm.cmd" -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $npm) {
        throw "ERROR: npm.cmd was not found on PATH."
    }
    if ($PSVersionTable.PSVersion.Major -lt 5 -or
        (-not $LocalDashboard -and -not (Get-Command Get-NetAdapter -ErrorAction SilentlyContinue)) -or
        -not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) -or
        -not (Get-Command Get-CimInstance -ErrorAction SilentlyContinue)) {
        throw "ERROR: Windows PowerShell 5.1 with network/process inspection cmdlets is required."
    }
    $serviceLock = [Threading.Mutex]::new($false, "Local\BAIKALDashboardServerLauncher")
    try {
        $lockHeld = $serviceLock.WaitOne(0)
    }
    catch [Threading.AbandonedMutexException] {
        $lockHeld = $true
        Write-Host "[WARN] Previous launcher exited unexpectedly; rechecking all listeners."
    }
    if (-not $lockHeld) {
        Stop-MobileLauncher "Another Dashboard launcher is checking or starting servers. Wait for it to finish and retry."
    }
    if ($LocalDashboard) {
        Initialize-PartialEnvironment
    }
    Initialize-ExpandedEnvironment
    if ([string]::IsNullOrWhiteSpace($env:BAIKAL_EXPANDED_ISOLATED_READY_ROOT)) {
        $env:BAIKAL_EXPANDED_ISOLATED_READY_ROOT = [Environment]::GetEnvironmentVariable("BAIKAL_EXPANDED_ISOLATED_READY_ROOT", "User")
    }

    $lan = $null
    try {
        if (-not $LocalDashboard) {
            $lan = Get-LanAddress
        }
    }
    catch {
        Write-Host "[WARN] LAN IPv4 auto-detection failed: $($_.Exception.Message)"
    }
    $python = [IO.Path]::GetFullPath($python)
    $npmPath = $npm.Source
    $pcUrl = "http://localhost:${frontendPort}/expanded-shadow"
    if ($LocalDashboard) {
        $pcUrl = "http://127.0.0.1:${frontendPort}/"
    }
    $lanIP = $null
    if ($null -ne $lan) {
        $lanIP = [string]$lan.IPAddress
    }
    $mobileUrl = "IP 자동 탐지 실패"
    if (-not [string]::IsNullOrWhiteSpace($lanIP)) {
        $mobileUrl = "http://${lanIP}:${frontendPort}/expanded-shadow"
    }

    Assert-BackendListener -PythonPath $python
    $null = Get-VerifiedFrontendBind -Frontend $frontend -LanIP $lanIP
    $backendListeners = @(Get-PortListeners -Port 8765)
    if ($backendListeners.Count -eq 0) {
        Write-Host "[INFO] Starting backend on 127.0.0.1:8765 ..."
        Start-BackendWindow -Root $root -Python $python
    }
    else {
        Write-Host "[INFO] Validating existing backend listener on port 8765 ..."
    }
    $backendState = Wait-ForBackend -PythonPath $python
    Assert-ExpandedDataApi -BaseUrl "http://127.0.0.1:8765"
    if ($LocalDashboard) {
        Assert-PartialApi -BaseUrl "http://127.0.0.1:8765"
    }

    $frontendListeners = @(Get-PortListeners -Port $frontendPort)
    if ($frontendListeners.Count -eq 0) {
        Write-Host "[INFO] Starting frontend on port $frontendPort ..."
        Start-FrontendWindow -Frontend $frontend -NpmPath $npmPath
    }
    else {
        Write-Host "[INFO] Validating existing frontend listener on port $frontendPort ..."
    }
    $frontendReady = Wait-ForFrontend -Frontend $frontend -LanIP $lanIP
    Assert-ExpandedDataApi -BaseUrl "http://127.0.0.1:${frontendPort}"
    if ($LocalDashboard) {
        Assert-PartialApi -BaseUrl "http://127.0.0.1:${frontendPort}"
        Write-Host "[READY] Read-only Dashboard: $pcUrl"
        Write-Host "Keep service windows open. Ctrl+C in each service window stops it."
        Write-Host "Closing this launcher does not stop servers. Already-running servers are reused."
        Write-Host "No analysis, collection, ledger or Scheduler operation was requested."
        Start-Process -FilePath $pcUrl | Out-Null
        exit 0
    }

    Write-Host ""
    Write-Host "============================================================"
    Write-Host " BAIKAL Stock Signal - Mobile LAN Pilot"
    Write-Host "============================================================"
    Write-Host ""
    Write-Host " Backend : $backendState"
    Write-Host "           127.0.0.1:8765"
    Write-Host ""
    Write-Host " Frontend: RUNNING"
    Write-Host "           $($frontendReady.BindAddress):${frontendPort}"
    Write-Host ""
    Write-Host " PC:"
    Write-Host " $pcUrl"
    Write-Host ""
    Write-Host " MOBILE:"
    Write-Host " $mobileUrl"
    if ([string]::IsNullOrWhiteSpace($lanIP)) {
        Write-Host " PC dashboard는 위 localhost 주소로 정상 사용 가능합니다."
    }
    Write-Host ""
    Write-Host " Expanded Shadow data status: $($frontendReady.Api.status) (service ready; data status may be MISSING/STALE)"
    Write-Host ""
    Write-Host " Smartphone:"
    if (-not [string]::IsNullOrWhiteSpace($lanIP)) {
        Write-Host " 스마트폰은 PC와 같은 Wi-Fi/LAN에 연결하세요."
        Write-Host " - Open the MOBILE address in the phone browser."
    }
    Write-Host ""
    Write-Host " Security:"
    Write-Host " - Trusted Private Wi-Fi only."
    Write-Host " - Do NOT expose this development server to the Internet."
    Write-Host " - READ ONLY UI is not network access control."
    Write-Host ""
    Write-Host " If the phone cannot connect, check the Windows network profile (Private),"
    Write-Host " inbound firewall access for port $frontendPort, same-LAN connectivity, and"
    Write-Host " AP/client isolation. 2.4GHz and 5GHz are fine when on the same LAN."
    Write-Host ""
    Write-Host " Keep any service windows opened by this launcher running."
    Write-Host " To stop a service started here, press Ctrl+C in its window or close it."
    Write-Host " Reused services are not managed by this launcher."
    Write-Host "============================================================"

    try {
        Start-Process -FilePath $pcUrl | Out-Null
    }
    catch {
        Write-Host "[WARN] Could not open the PC browser automatically: $($_.Exception.Message)"
        Write-Host "       Open $pcUrl manually."
    }
    exit 0
}
catch {
    $message = $_.Exception.Message
    if ($message.StartsWith("STOP: ")) {
        Write-Host "[STOP] $($message.Substring(6))" -ForegroundColor Red
    }
    elseif ($message.StartsWith("ERROR: ")) {
        Write-Host "[ERROR] $($message.Substring(7))" -ForegroundColor Red
    }
    else {
        Write-Host "[ERROR] Dashboard launcher failed: $message" -ForegroundColor Red
    }
    exit 1
}
finally {
    if ($lockHeld) {
        $serviceLock.ReleaseMutex()
    }
    if ($null -ne $serviceLock) {
        $serviceLock.Dispose()
    }
}
