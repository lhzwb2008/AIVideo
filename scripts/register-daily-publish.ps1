#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.SYNOPSIS
  Register a Windows scheduled task to run make-and-publish daily.

.EXAMPLE
  .\scripts\register-daily-publish.ps1
  .\scripts\register-daily-publish.ps1 -At 17:30
  .\scripts\register-daily-publish.ps1 -At 17:30 -Count 1
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
    throw "Wrapper not found: $Wrapper"
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

$TaskUser = $RunAsUser
if ($TaskUser -notmatch '\\') {
    $TaskUser = "$env:USERDOMAIN\$TaskUser"
}

# PS 5.1 / older Server: LogonType goes on Principal, not Register-ScheduledTask
$Principal = New-ScheduledTaskPrincipal -UserId $TaskUser -LogonType Interactive -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Force | Out-Null

Write-Host 'Scheduled task registered.' -ForegroundColor Green
Write-Host "  TaskName : $TaskName"
Write-Host "  Schedule : daily at $At"
Write-Host "  User     : $TaskUser (Interactive)"
Write-Host "  Command  : powershell.exe $PsArgs"
Write-Host "  WorkDir  : $Root"
Write-Host "  Logs     : $Root\logs\scheduled\"
if ($Count -gt 0) {
    Write-Host "  Count    : $Count video(s) per run"
} else {
    Write-Host '  Count    : from .env AIVIDEO_MAX_VIDEOS_PER_RUN (weekday/weekend default)'
}
Write-Host ''
Write-Host 'Manage:'
Write-Host "  Run now   : Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Status    : Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "  View logs : Get-ChildItem $Root\logs\scheduled\ | Sort-Object LastWriteTime -Descending | Select-Object -First 3"
Write-Host "  Remove    : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host ''
Write-Host 'NOTE: Interactive logon - keep Administrator signed in to RDP desktop for browser publish.' -ForegroundColor Yellow
