param(
    [Parameter(Mandatory = $true)]
    [string]$RepositoryRoot
)

$ErrorActionPreference = "Stop"

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
        Stop-MobileLauncher "No active physical Wi-Fi/Ethernet IPv4 with a default gateway was found."
    }

    $bestMetric = ($candidateRows | Measure-Object -Property Metric -Minimum).Minimum
    $bestCandidates = @($candidateRows | Where-Object { $_.Metric -eq $bestMetric } |
        Sort-Object Name, IPAddress -Unique)
    if ($bestCandidates.Count -ne 1) {
        Write-Host "[STOP] LAN IPv4 is ambiguous; no address was selected. Candidate adapters:"
        foreach ($candidate in $candidateRows | Sort-Object Metric, Name, IPAddress -Unique) {
            Write-Host ("       {0} ({1})  {2}  gateway={3} metric={4}" -f $candidate.Name, $candidate.Description, $candidate.IPAddress, $candidate.Gateway, $candidate.Metric)
        }
        throw "STOP: Select/disable the appropriate network connection, then run the launcher again."
    }

    return $bestCandidates[0]
}

function Start-BackendWindow {
    param([string]$Root, [string]$Python)
    $command = '/k cd /d "' + $Root + '" && "' + $Python + '" -m dashboard.api'
    Start-Process -FilePath $env:ComSpec -ArgumentList $command -WorkingDirectory $Root -WindowStyle Normal | Out-Null
}

function Start-FrontendWindow {
    param([string]$Frontend, [string]$NpmPath)
    $command = '/k cd /d "' + $Frontend + '" && "' + $NpmPath + '" run dev -- --host 0.0.0.0 --strictPort'
    Start-Process -FilePath $env:ComSpec -ArgumentList $command -WorkingDirectory $Frontend -WindowStyle Normal | Out-Null
}

function Wait-ForBackend {
    param([string]$PythonPath)
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        $listeners = @(Get-PortListeners -Port 8765)
        if ($listeners.Count -gt 0) {
            $processIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
            $addresses = @($listeners | Select-Object -ExpandProperty LocalAddress -Unique)
            if ($processIds.Count -ne 1 -or ($addresses | Where-Object { $_ -ne "127.0.0.1" }).Count -gt 0) {
                Stop-MobileLauncher "Port 8765 has an unexpected listener or bind address."
            }

            if (-not (Test-BackendProcess -ProcessId $processIds[0] -PythonPath $PythonPath)) {
                Stop-MobileLauncher "Port 8765 is already in use by an unknown process."
            }

            $health = Test-HttpJson -Url "http://127.0.0.1:8765/api/dashboard/health" -Validate {
                param($payload)
                return $payload -is [System.Management.Automation.PSCustomObject]
            }
            if ($null -ne $health) {
                return "RUNNING"
            }
        }
        Start-Sleep -Seconds 1
    }
    Stop-MobileLauncher "Backend did not become ready on 127.0.0.1:8765 within 30 bounded attempts."
}

function Wait-ForFrontend {
    param(
        [string]$Frontend,
        [string]$LanIP
    )
    $viteEntry = [IO.Path]::GetFullPath((Join-Path $Frontend "node_modules\vite\bin\vite.js"))
    $apiAttempts = 0
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        $listeners = @(Get-PortListeners -Port 5173)
        if ($listeners.Count -gt 0) {
            $processIds = @($listeners | Select-Object -ExpandProperty OwningProcess -Unique)
            if ($processIds.Count -ne 1) {
                Stop-MobileLauncher "Port 5173 has multiple listener processes; refusing to guess."
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
                        $actualViteEntry,
                        $viteEntry,
                        [StringComparison]::OrdinalIgnoreCase
                    )
                }
                catch {
                    $entryMatchesRepository = $false
                }
            }
            if ([IO.Path]::GetFileName($actualPath) -ine "node.exe" -or
                -not $entryMatchesRepository) {
                Stop-MobileLauncher "Port 5173 is already in use by an unknown process."
            }

            $addresses = @($listeners | Select-Object -ExpandProperty LocalAddress -Unique)
            if ($addresses -contains "127.0.0.1" -and
                ($addresses | Where-Object { $_ -ne "127.0.0.1" }).Count -eq 0) {
                Stop-MobileLauncher "Existing frontend is localhost-only.`nClose the existing frontend window and run`nrun_mobile_dashboard.bat again."
            }
            if ($addresses.Count -ne 1 -or
                ($addresses[0] -ne "0.0.0.0" -and $addresses[0] -ne $LanIP)) {
                Stop-MobileLauncher "Port 5173 is not verifiably bound for IPv4 LAN access (IPv6-only or unknown bind)."
            }

            $page = $null
            try {
                $page = Invoke-WebRequest -Uri "http://localhost:5173/expanded-shadow" -UseBasicParsing -TimeoutSec 5
            }
            catch {
                $page = $null
            }
            if ($null -ne $page -and $page.StatusCode -eq 200 -and $page.Content -match '(?i)<html') {
                $apiAttempts++
                $api = Test-HttpJson -Url "http://localhost:5173/api/dashboard/expanded-shadow" -TimeoutSec 20 -Validate {
                    param($payload)
                    return $payload -is [System.Management.Automation.PSCustomObject] -and
                        $payload.mode -eq "EXPANDED_SHADOW" -and
                        $null -ne $payload.PSObject.Properties["status"] -and
                        $null -ne $payload.PSObject.Properties["run_summary"]
                }
                if ($null -ne $api) {
                    return [pscustomobject]@{
                        Api = $api
                        BindAddress = $addresses[0]
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
        -not (Get-Command Get-NetAdapter -ErrorAction SilentlyContinue) -or
        -not (Get-Command Get-NetTCPConnection -ErrorAction SilentlyContinue) -or
        -not (Get-Command Get-CimInstance -ErrorAction SilentlyContinue)) {
        throw "ERROR: Windows PowerShell 5.1 with network/process inspection cmdlets is required."
    }

    $lan = Get-LanAddress
    $python = [IO.Path]::GetFullPath($python)
    $npmPath = $npm.Source
    $pcUrl = "http://localhost:5173/expanded-shadow"
    $mobileUrl = "http://$($lan.IPAddress):5173/expanded-shadow"

    $backendListeners = @(Get-PortListeners -Port 8765)
    if ($backendListeners.Count -eq 0) {
        Write-Host "[INFO] Starting backend on 127.0.0.1:8765 ..."
        Start-BackendWindow -Root $root -Python $python
    }
    else {
        Write-Host "[INFO] Validating existing backend listener on port 8765 ..."
    }
    $backendState = Wait-ForBackend -PythonPath $python

    $frontendListeners = @(Get-PortListeners -Port 5173)
    if ($frontendListeners.Count -eq 0) {
        Write-Host "[INFO] Starting LAN frontend on 0.0.0.0:5173 ..."
        Start-FrontendWindow -Frontend $frontend -NpmPath $npmPath
    }
    else {
        Write-Host "[INFO] Validating existing frontend listener on port 5173 ..."
    }
    $frontendReady = Wait-ForFrontend -Frontend $frontend -LanIP $lan.IPAddress

    Write-Host ""
    Write-Host "============================================================"
    Write-Host " BAIKAL Stock Signal - Mobile LAN Pilot"
    Write-Host "============================================================"
    Write-Host ""
    Write-Host " Backend : $backendState"
    Write-Host "           127.0.0.1:8765"
    Write-Host ""
    Write-Host " Frontend: RUNNING"
    Write-Host "           $($frontendReady.BindAddress):5173"
    Write-Host ""
    Write-Host " PC:"
    Write-Host " $pcUrl"
    Write-Host ""
    Write-Host " MOBILE:"
    Write-Host " $mobileUrl"
    Write-Host ""
    Write-Host " Expanded Shadow data status: $($frontendReady.Api.status) (service ready; data status may be MISSING/STALE)"
    Write-Host ""
    Write-Host " Smartphone:"
    Write-Host " - Connect the phone to the same trusted Wi-Fi/LAN."
    Write-Host " - Open the MOBILE address in the phone browser."
    Write-Host ""
    Write-Host " Security:"
    Write-Host " - Trusted Private Wi-Fi only."
    Write-Host " - Do NOT expose this development server to the Internet."
    Write-Host " - READ ONLY UI is not network access control."
    Write-Host ""
    Write-Host " If the phone cannot connect, check the Windows network profile (Private),"
    Write-Host " inbound firewall access for port 5173, same-LAN connectivity, and"
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
        Write-Host "[ERROR] Mobile launcher failed: $message" -ForegroundColor Red
    }
    exit 1
}
