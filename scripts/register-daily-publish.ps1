#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
  注册 Windows 计划任务，每日自动运行 make-and-publish

.DESCRIPTION
  默认每天 17:30 执行（适合下班前出片）。
  浏览器发布（抖音/小红书/视频号等）需用户已登录桌面；任务使用 Interactive 模式。

.EXAMPLE
  .\scripts\register-daily-publish.ps1
  .\scripts\register-daily-publish.ps1 -At 17:30
  .\scripts\register-daily-publish.ps1 -At 17:30 -Count 1
  .\scripts\register-daily-publish.ps1 -At 08:00 -Count 5 -TaskName AIVideoMorning
#>
param(
    [string]$At = '17:30',
    [int]$Count = 0,
    [string]$TaskName = 'AIVideoMakeAndPublish',
    [string]$RunAsUser = $env:USERNAME
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Wrapper = Join-Path $Root 'scripts\run-scheduled-publish.ps1'

if (-not (Test-Path $Wrapper)) {
    throw "未找到 $Wrapper"
}

$CountArg = if ($Count -gt 0) { " -Count $Count" } else { '' }
$PsArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$Wrapper`"$CountArg"
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $PsArgs -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Daily -At $At

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 8)

$LogonType = 'Interactive'

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -User $RunAsUser `
    -LogonType $LogonType `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host '已注册计划任务' -ForegroundColor Green
Write-Host "  名称: $TaskName"
Write-Host "  时间: 每天 $At"
Write-Host "  用户: $RunAsUser ($LogonType)"
Write-Host "  命令: powershell.exe $PsArgs"
Write-Host "  工作目录: $Root"
Write-Host "  日志目录: $Root\logs\scheduled\"
if ($Count -gt 0) {
    Write-Host "  条数: $Count"
} else {
    Write-Host '  条数: 由 .env 的 AIVIDEO_MAX_VIDEOS_PER_RUN / 工作日槽位决定'
}
Write-Host ''
Write-Host '管理命令:'
Write-Host "  立即试跑: Start-ScheduledTask -TaskName $TaskName"
Write-Host "  查看状态: Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "  查看日志: Get-ChildItem $Root\logs\scheduled\ | Sort-Object LastWriteTime -Descending | Select-Object -First 3"
Write-Host "  删除任务: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host ''
Write-Host '注意: Interactive 模式需保持 Administrator 已登录 RDP 桌面，浏览器发布才能成功。' -ForegroundColor Yellow
