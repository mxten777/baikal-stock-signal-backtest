param(
    [ValidateSet("Setup", "Verify", "Preview", "Execute")]
    [string]$Mode = "Verify",
    [string]$SecretDirectory = (Join-Path $env:LOCALAPPDATA "BAIKAL\Dart")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$secure = $null
$pointer = [IntPtr]::Zero
$child = $null
$start = $null
$plain = $null
$cipher = $null
$exitCode = 1
$code = "INITIALIZATION_FAILED"

function Assert-PrivatePath {
    param([string]$Path, [System.Security.Principal.SecurityIdentifier]$Sid)
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "REPARSE_POINT_NOT_ALLOWED"
    }
    $acl = Get-Acl -LiteralPath $Path
    if (-not $acl.AreAccessRulesProtected -or
        $acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $Sid.Value) {
        throw "PRIVATE_ACL_REQUIRED"
    }
    $rules = @($acl.GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 1 -or $rules[0].IdentityReference.Value -ne $Sid.Value -or
        $rules[0].AccessControlType -ne [System.Security.AccessControl.AccessControlType]::Allow -or
        $rules[0].FileSystemRights -ne [System.Security.AccessControl.FileSystemRights]::FullControl) {
        throw "PRIVATE_ACL_REQUIRED"
    }
}

function Set-PrivatePath {
    param([string]$Path, [System.Security.Principal.SecurityIdentifier]$Sid, [bool]$Directory)
    $acl = Get-Acl -LiteralPath $Path
    if ($acl.GetOwner([System.Security.Principal.SecurityIdentifier]).Value -ne $Sid.Value) {
        throw "CURRENT_USER_OWNERSHIP_REQUIRED"
    }
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($existing in @($acl.GetAccessRules($true, $false, [System.Security.Principal.SecurityIdentifier]))) {
        $acl.RemoveAccessRuleSpecific($existing)
    }
    if ($Directory) {
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $Sid, "FullControl", "ContainerInherit, ObjectInherit", "None", "Allow"
        )
    }
    else {
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($Sid, "FullControl", "Allow")
    }
    $acl.AddAccessRule($rule)
    Set-Acl -LiteralPath $Path -AclObject $acl
    Assert-PrivatePath -Path $Path -Sid $Sid
}

try {
    $code = "ACCOUNT_OR_PATH_INVALID"
    Import-Module (Join-Path $PSHOME "Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1") -ErrorAction Stop
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    if ($identity.Name -ine "BAIKAL777\baika") {
        throw "EXPECTED_BAIKA_ACCOUNT"
    }
    $sid = $identity.User
    $root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
    $directory = [IO.Path]::GetFullPath($SecretDirectory).TrimEnd("\")
    if ($directory.Equals($root, [StringComparison]::OrdinalIgnoreCase) -or
        $directory.StartsWith($root + "\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "SECRET_MUST_BE_OUTSIDE_REPOSITORY"
    }
    # Reject redirected ancestors before reading or creating the private directory.
    $ancestor = $directory
    while ($ancestor) {
        if (Test-Path -LiteralPath $ancestor) {
            $item = Get-Item -LiteralPath $ancestor -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "REPARSE_POINT_NOT_ALLOWED"
            }
        }
        $ancestor = [IO.Path]::GetDirectoryName($ancestor)
    }
    $path = Join-Path $directory "dart_api_key.dpapi"
    if ($Mode -eq "Setup") {
        $code = "SECRET_SETUP_FAILED"
        if (Test-Path -LiteralPath $path) {
            throw "SECRET_ALREADY_EXISTS"
        }
        if (-not (Test-Path -LiteralPath $directory)) {
            $code = "SECRET_DIRECTORY_CREATE_FAILED"
            New-Item -ItemType Directory -Path $directory -Force | Out-Null
            $code = "SECRET_DIRECTORY_ACL_FAILED"
            Set-PrivatePath -Path $directory -Sid $sid -Directory $true
        }
        $code = "SECRET_DIRECTORY_VALIDATION_FAILED"
        Assert-PrivatePath -Path $directory -Sid $sid
        $code = "SECRET_INPUT_FAILED"
        if (-not [string]::IsNullOrWhiteSpace($env:DART_API_KEY)) {
            $secure = ConvertTo-SecureString -String $env:DART_API_KEY.Trim() -AsPlainText -Force
        }
        else {
            $secure = Read-Host "DART API key (hidden input)" -AsSecureString
        }
        if ($secure.Length -eq 0) {
            throw "EMPTY_KEY"
        }
        # Without -Key, Windows uses current-user DPAPI, not portable encryption.
        $cipher = ConvertFrom-SecureString -SecureString $secure
        $bytes = [Text.Encoding]::ASCII.GetBytes($cipher)
        $stream = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
        try {
            $stream.Write($bytes, 0, $bytes.Length)
            $stream.Flush($true)
        }
        finally {
            $stream.Dispose()
        }
        Set-PrivatePath -Path $path -Sid $sid -Directory $false
        $secure.Dispose()
        $secure = $null
    }
    $code = "SECRET_READ_OR_DECRYPT_FAILED"
    Assert-PrivatePath -Path $directory -Sid $sid
    Assert-PrivatePath -Path $path -Sid $sid
    $cipher = [IO.File]::ReadAllText($path, [Text.Encoding]::ASCII)
    $secure = ConvertTo-SecureString -String $cipher
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ([string]::IsNullOrWhiteSpace($plain)) {
        throw "EMPTY_KEY"
    }
    $code = "CHILD_PROCESS_FAILED"
    $start = New-Object System.Diagnostics.ProcessStartInfo
    $start.FileName = Join-Path $root ".venv\Scripts\python.exe"
    $start.WorkingDirectory = $root
    $start.UseShellExecute = $false
    $start.EnvironmentVariables["DART_API_KEY"] = $plain
    if ($Mode -in @("Setup", "Verify")) {
        $start.Arguments = '-B -c "import os,sys; present=bool(os.environ.get(''DART_API_KEY'','''').strip()); print(''CHILD_KEY_PRESENT='' + str(present)); sys.exit(0 if present else 1)"'
    }
    elseif ($Mode -eq "Preview") {
        $start.Arguments = "-B -m scripts.dart_disclosure_scheduler"
    }
    else {
        $start.Arguments = "-B -m scripts.dart_disclosure_scheduler --execute"
    }
    $child = [Diagnostics.Process]::Start($start)
    $child.WaitForExit()
    $exitCode = $child.ExitCode
    if ($exitCode -eq 0) {
        [pscustomobject]@{
            verdict = "PASS"
            mode = $Mode
            dpapi_scope = "CurrentUser"
            account = $identity.Name
            child_environment_verified = ($Mode -in @("Setup", "Verify"))
        } | ConvertTo-Json -Compress
    }
    else {
        [pscustomobject]@{verdict = "FAIL"; error_code = $code; child_exit_code = $exitCode} |
            ConvertTo-Json -Compress
    }
}
catch {
    # Exception bodies may contain secret input; emit only the local stage code.
    [pscustomobject]@{
        verdict = "FAIL"
        error_code = $code
        error_line = $_.InvocationInfo.ScriptLineNumber
        error_type = $_.Exception.GetType().Name
    } | ConvertTo-Json -Compress
    $exitCode = 1
}
finally {
    if ($null -ne $start) { $start.EnvironmentVariables.Remove("DART_API_KEY") }
    if ($pointer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    if ($null -ne $secure) { $secure.Dispose() }
    if ($null -ne $child) { $child.Dispose() }
    $plain = $null
    $cipher = $null
}
exit $exitCode
