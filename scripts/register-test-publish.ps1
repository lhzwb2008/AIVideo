#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
  注册「只跑一次」的测试计划任务：下一分钟执行 make-and-publish 1 条，跑完自动注销。

  不影响已注册的 AIVideoMakeAndPublish（日常定时，如每天 15:30 跑 3 条）。

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
    # 对齐到整分，避免 15:30:47 这种触发时间
    $At = Get-Date -Year $At.Year -Month $At.Month -Day $At.Day `
        -Hour $At.Hour -Minute $At.Minute -Second 0
}
if ($At -le (Get-Date)) {
    throw "触发时间必须在未来。当前: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')，指定: $($At.ToString('yyyy-MM-dd HH:mm:ss'))"
}

$DailyTask = 'AIVideoMakeAndPublish'
$daily = Get-ScheduledTask -TaskName $DailyTask -ErrorAction SilentlyContinue
if (-not $daily) {
    Write-Host "NOTE: 未找到日常任务 $DailyTask（不影响本次测试）。" -ForegroundColor Yellow
} else {
    $dailyInfo = $daily | Get-ScheduledTaskInfo
    Write-Host "日常任务保留: $DailyTask（NextRun: $($dailyInfo.NextRunTime)）" -ForegroundColor DarkGray
}

# 若上次测试任务残留，先清掉
$old = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($old) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed stale test task: $TaskName" -ForegroundColor Yellow
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
Write-Host "  TaskName : $TaskName  (NOT $DailyTask)"
Write-Host "  Runs at  : $($At.ToString('yyyy-MM-dd HH:mm:ss'))  (once, 1 video)"
Write-Host "  After run: task auto-removed by run-test-publish.ps1"
Write-Host "  User     : $TaskUser (Interactive — keep RDP desktop logged in)"
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
