#Requires -Version 5.1
<#
.SYNOPSIS
  Scheduled-task entry: run make-and-publish and log to logs\scheduled\

.EXAMPLE
  .\scripts\run-scheduled-publish.ps1
  .\scripts\run-scheduled-publish.ps1 -Count 1
#>
param(
    [int]$Count = 0,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$env:ROOT = $Root

$LogDir = Join-Path $Root 'logs\scheduled'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir ("{0}.log" -f (Get-Date -Format 'yyyyMMdd_HHmmss'))

Write-Host '==> AIVideo scheduled run' -ForegroundColor Cyan
Write-Host "    Log: $LogFile"
Write-Host "    Started: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host ''

$Main = Join-Path $Root 'make-and-publish.ps1'
if (-not (Test-Path $Main)) {
    throw "make-and-publish.ps1 not found: $Main"
}

$RunArgs = @()
if ($Count -gt 0) {
    $RunArgs += [string]$Count
}
if ($ExtraArgs) {
    $RunArgs += $ExtraArgs
}

$ExitCode = 0
try {
    Start-Transcript -Path $LogFile -Force | Out-Null
    & $Main @RunArgs
    $ExitCode = $LASTEXITCODE
    if ($ExitCode -ne 0) {
        Write-Host "make-and-publish exit code: $ExitCode" -ForegroundColor Red
    }
} catch {
    $ExitCode = 1
    $_ | Out-String | Write-Host -ForegroundColor Red
    "FATAL: $_" | Add-Content -Path $LogFile -Encoding UTF8
} finally {
    try { Stop-Transcript | Out-Null } catch { }
    Write-Host ''
    Write-Host "    Finished: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "    Log: $LogFile"
}

exit $ExitCode
