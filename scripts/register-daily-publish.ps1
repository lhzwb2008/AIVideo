#Requires -Version 5.1
<#
.SYNOPSIS
  注册 Windows 计划任务，每日自动运行 make-and-publish

.EXAMPLE
  .\scripts\register-daily-publish.ps1 -At 08:00
  .\scripts\register-daily-publish.ps1 -At 07:30 -Count 5 -TaskName AIVideoDaily
#>
param(
    [string]$At = '08:00',
    [int]$Count = 0,
    [string]$TaskName = 'AIVideoMakeAndPublish',
    [string]$RunAsUser = $env:USERNAME
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Script = Join-Path $Root 'make-and-publish.ps1'

if (-not (Test-Path $Script)) {
    throw "未找到 $Script"
}

$CountArg = if ($Count -gt 0) { " $Count" } else { '' }
$Args = "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"$CountArg"
$Action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $Args -WorkingDirectory $Root
$Trigger = New-ScheduledTaskTrigger -Daily -At $At

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 6)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -User $RunAsUser `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "已注册计划任务: $TaskName"
Write-Host "  时间: 每天 $At"
Write-Host "  命令: powershell.exe $Args"
Write-Host "  工作目录: $Root"
Write-Host ""
Write-Host "管理:"
Write-Host "  立即运行: Start-ScheduledTask -TaskName $TaskName"
Write-Host "  查看日志: Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "  删除任务: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
