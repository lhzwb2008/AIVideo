#Requires -Version 5.1
<#
.SYNOPSIS
  Register a Windows scheduled task to run make-and-publish daily.

.EXAMPLE
  .\register-daily-publish.ps1
  .\register-daily-publish.ps1 -At 17:30
  .\register-daily-publish.ps1 -At 17:30 -Count 1
  .\register-daily-publish.ps1 -Check
#>
param(
    [string]$At = '17:30',
    [int]$Count = 0,
    [string]$TaskName = 'AIVideoMakeAndPublish',
    [string]$RunAsUser = $env:USERNAME,
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$Wrapper = Join-Path $Root 'scripts\run-scheduled-publish.ps1'

$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) {
    Write-Host "Registered: $TaskName" -ForegroundColor Green
    $info = $Existing | Get-ScheduledTaskInfo
    $trigger = $Existing.Triggers | Select-Object -First 1
    Write-Host "  State       : $($Existing.State)"
    if ($trigger -and $trigger.StartBoundary) {
        Write-Host "  TriggerAt   : $([datetime]$trigger.StartBoundary | Get-Date -Format 'HH:mm')"
    }
    Write-Host "  LastRun     : $($info.LastRunTime)  (result: $($info.LastTaskResult))"
    Write-Host "  NextRun     : $($info.NextRunTime)"
} else {
    Write-Host "Not registered: $TaskName" -ForegroundColor Yellow
}

if ($Check) {
    return
}

if (-not (Test-Path $Wrapper)) {
    throw "Wrapper not found: $Wrapper"
}

$isAdmin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw 'Administrator privileges required. Re-run PowerShell as Administrator.'
}

Write-Host ''
Write-Host '==> Registering scheduled task...' -ForegroundColor Cyan

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
Write-Host "  Check     : .\register-daily-publish.ps1 -Check"
Write-Host "  Run now   : Start-ScheduledTask -TaskName $TaskName"
Write-Host "  Status    : Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo"
Write-Host "  View logs : Get-ChildItem $Root\logs\scheduled\ | Sort-Object LastWriteTime -Descending | Select-Object -First 3"
Write-Host "  Remove    : Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
Write-Host ''
Write-Host 'NOTE: Interactive logon - keep Administrator signed in to RDP desktop for browser publish.' -ForegroundColor Yellow
