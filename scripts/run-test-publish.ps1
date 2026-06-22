#Requires -Version 5.1
<#
.SYNOPSIS
  One-shot test scheduled-task entry: run make-and-publish x1, then self-remove the test task.

  Registered by register-test-publish.ps1; do not mix with AIVideoMakeAndPublish (daily 15:30).

.EXAMPLE
  .\scripts\run-test-publish.ps1
  .\scripts\run-test-publish.ps1 -TaskName AIVideoMakeAndPublishTest
#>
param(
    [string]$TaskName = 'AIVideoMakeAndPublishTest',
    [int]$Count = 1
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$env:ROOT = $Root

$Wrapper = Join-Path $Root 'scripts\run-scheduled-publish.ps1'
if (-not (Test-Path $Wrapper)) {
    throw "Wrapper not found: $Wrapper"
}

Write-Host '==> AIVideo one-shot TEST scheduled run' -ForegroundColor Magenta
Write-Host "    TaskName : $TaskName"
Write-Host "    Count    : $Count"
Write-Host "    Started  : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host ''

$ExitCode = 0
try {
    & $Wrapper -Count $Count
    if ($null -ne $LASTEXITCODE) {
        $ExitCode = [int]$LASTEXITCODE
    }
} catch {
    $ExitCode = 1
    Write-Host "TEST run failed: $_" -ForegroundColor Red
} finally {
    Write-Host ''
    try {
        $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($existing) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Write-Host "Test task removed: $TaskName" -ForegroundColor Green
        } else {
            Write-Host "Test task already removed: $TaskName" -ForegroundColor DarkGray
        }
    } catch {
        Write-Host ('Could not remove test task {0}: {1}' -f $TaskName, $_) -ForegroundColor Yellow
        Write-Host "Remove manually: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
    }
    Write-Host "    Finished : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host "    Logs     : $Root\logs\scheduled\"
}

exit $ExitCode
