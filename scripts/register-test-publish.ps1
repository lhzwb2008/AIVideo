#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Register a one-shot test task: run make-and-publish x1 at the next minute, then self-remove.

  Does NOT touch AIVideoMakeAndPublish (daily job, e.g. 15:30 x3).

.EXAMPLE
  .\scripts\register-test-publish.ps1
  .\scripts\register-test-publish.ps1 -MinutesFromNow 2
  .\scripts\register-test-publish.ps1 -At '16:05'
#>
param(
    [datetime]$At,
    [int]$MinutesFromNow = 1,
    [string]$TaskName = 'AIVideoMakeAndPublishTest',
    [string]$RunAsUser = $env:USERNAME
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Runner = Join-Path $Root 'scripts\run-test-publish.ps1'

if (-not (Test-Path $Runner)) {
    throw "Runner not found: $Runner"
}

if (-not $At) {
    $At = (Get-Date).AddMinutes($MinutesFromNow)
    $At = Get-Date -Year $At.Year -Month $At.Month -Day $At.Day `
        -Hour $At.Hour -Minute $At.Minute -Second 0
}
if ($At -le (Get-Date)) {
    $nowStr = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $atStr = $At.ToString('yyyy-MM-dd HH:mm:ss')
    throw "Trigger time must be in the future. Now=$nowStr At=$atStr"
}

$DailyTask = 'AIVideoMakeAndPublish'
$daily = Get-ScheduledTask -TaskName $DailyTask -ErrorAction SilentlyContinue
if (-not $daily) {
    Write-Host ('NOTE: daily task not found ({0}); test can still run.' -f $DailyTask) -ForegroundColor Yellow
} else {
    $dailyInfo = $daily | Get-ScheduledTaskInfo
    $nextRun = $dailyInfo.NextRunTime
    Write-Host ('Daily task unchanged: {0}  next run at {1}' -f $DailyTask, $nextRun) -ForegroundColor DarkGray
}

$old = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($old) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host ('Removed stale test task: {0}' -f $TaskName) -ForegroundColor Yellow
}

$PsArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$Runner`" -TaskName $TaskName -Count 1"
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $PsArgs -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Once -At $At

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)

$TaskUser = $RunAsUser
if ($TaskUser -notmatch '\\') {
    $TaskUser = "$env:USERDOMAIN\$TaskUser"
}

$Principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType Interactive -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Force | Out-Null

Write-Host ''
Write-Host 'One-shot TEST task registered.' -ForegroundColor Green
Write-Host ('  TaskName : {0}  (NOT {1})' -f $TaskName, $DailyTask)
Write-Host ('  Runs at  : {0}  (once, 1 video)' -f $At.ToString('yyyy-MM-dd HH:mm:ss'))
Write-Host '  After run: task auto-removed by run-test-publish.ps1'
Write-Host ('  User     : {0} (Interactive — keep RDP desktop logged in)' -f $TaskUser)
Write-Host "  Command  : powershell.exe $PsArgs"
Write-Host "  Logs     : $Root\logs\scheduled\"
Write-Host ''
Write-Host 'Check before trigger:'
Write-Host "  Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host ''
Write-Host 'After run (~20+ min):'
Write-Host "  Get-ChildItem $Root\logs\scheduled\*.log | Sort-Object LastWriteTime -Descending | Select-Object -First 1"
Write-Host ''
Write-Host 'Manual cleanup (if task stuck):'
Write-Host "  Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host ''
Write-Host 'Daily task unchanged — tomorrow still runs at your registered time.' -ForegroundColor Cyan
