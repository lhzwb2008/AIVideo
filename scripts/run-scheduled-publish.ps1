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
$StartedAt = Get-Date

function Write-RunLog {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

Write-RunLog '==> AIVideo scheduled run'
Write-RunLog "Log: $LogFile"
Write-RunLog "WorkDir: $Root"

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
    # Avoid Start-Transcript (file-lock / PS 5.1 false errors); tee output to log directly
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $Main @RunArgs 2>&1 | ForEach-Object {
        $_.ToString() | Add-Content -LiteralPath $LogFile -Encoding UTF8
        Write-Host $_
    }
    $ErrorActionPreference = $prevEap
    if ($null -ne $LASTEXITCODE) {
        $ExitCode = [int]$LASTEXITCODE
    }
    if ($ExitCode -ne 0) {
        Write-RunLog "make-and-publish exit code: $ExitCode"
    }
} catch {
    $ExitCode = 1
    Write-RunLog "FATAL: $_"
} finally {
    $DateTag = Get-Date -Format 'yyyyMMdd'
    $ArchiveDir = Join-Path $Root "archive\published\$DateTag\zh"
    $Archived = @()
    if (Test-Path $ArchiveDir) {
        $Archived = Get-ChildItem -Path $ArchiveDir -Filter '*.mp4' -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -ge $StartedAt.AddMinutes(-2) } |
            Sort-Object LastWriteTime
    }
    Write-RunLog "Finished (exit=$ExitCode, elapsed=$([math]::Round(((Get-Date) - $StartedAt).TotalMinutes, 1)) min)"
    if ($Archived.Count -gt 0) {
        Write-RunLog "Archived this run: $($Archived.Count) video(s)"
        foreach ($v in $Archived) {
            Write-RunLog "  - $($v.Name) @ $($v.LastWriteTime.ToString('HH:mm:ss'))"
        }
    } else {
        Write-RunLog 'Archived this run: 0 videos (check output\zh\ or errors above)'
    }
    Write-RunLog "Log: $LogFile"
}

exit $ExitCode
