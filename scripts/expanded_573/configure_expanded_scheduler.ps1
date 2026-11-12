$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Administrator PowerShell is required to update this existing task.'
}
if ((Get-TimeZone).Id -ne 'Korea Standard Time') {
    throw 'Expected Korea Standard Time; no system timezone change is permitted.'
}
$name = 'BAIKAL Stock Expanded Scheduler'
$daily = 'BAIKAL Stock Daily Scheduler'
$dual = 'BAIKAL Stock DUAL Scheduler'
$dailyXml = Export-ScheduledTask -TaskName $daily
$dualXml = Export-ScheduledTask -TaskName $dual
foreach ($other in @($daily, $dual)) {
    if ([string](Get-ScheduledTask -TaskName $other).State -ne 'Disabled') {
        throw 'Daily and DUAL must already be Disabled; this script does not change them.'
    }
}
$task = Get-ScheduledTask -TaskName $name
if ([string]$task.State -ne 'Disabled') {
    throw 'Expanded must be Disabled before configuration.'
}
$wrapper = Join-Path $PSScriptRoot 'run_expanded_with_log.ps1'
& "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive `
    -ExecutionPolicy Bypass -File $wrapper -SourceDate '2026-11-12' -CheckOnly
if ($LASTEXITCODE -ne 0) { throw 'Read-only wrapper check failed.' }
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + $wrapper + '"') `
    -WorkingDirectory $PSScriptRoot
$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '19:30'
$next = (Get-Date).Date.AddHours(19).AddMinutes(30)
if ($next -le (Get-Date)) { $next = $next.AddDays(1) }
while ($next.DayOfWeek -in @([DayOfWeek]::Saturday, [DayOfWeek]::Sunday)) {
    $next = $next.AddDays(1)
}
$trigger.StartBoundary = $next.ToString("yyyy-MM-dd'T'HH:mm:ss") + '+09:00'
Set-ScheduledTask -TaskName $name -Action $action -Trigger $trigger | Out-Null
$updated = Get-ScheduledTask -TaskName $name
if (@($updated.Triggers).Count -ne 1 -or
    $updated.Triggers[0].StartBoundary -ne $trigger.StartBoundary -or
    $updated.Triggers[0].DaysOfWeek -ne 62 -or
    $updated.Actions[0].Arguments -ne $action.Arguments -or
    $updated.Actions[0].WorkingDirectory -ne $PSScriptRoot -or
    $updated.Settings.RestartCount -ne 0 -or [string]$updated.State -ne 'Disabled' -or
    (Export-ScheduledTask -TaskName $daily) -ne $dailyXml -or
    (Export-ScheduledTask -TaskName $dual) -ne $dualXml) {
    throw 'Configuration verification failed; Expanded was not enabled.'
}
Enable-ScheduledTask -TaskName $name | Out-Null
$final = Get-ScheduledTask -TaskName $name
if ([string]$final.State -ne 'Ready') { throw 'Expanded task did not become Ready.' }
[pscustomobject]@{
    State = [string]$final.State
    NextRunTime = (Get-ScheduledTaskInfo -TaskName $name).NextRunTime
    Arguments = $final.Actions[0].Arguments
    RetryCount = $final.Settings.RestartCount
}
