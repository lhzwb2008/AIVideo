#Requires -Version 5.1
# AI财知道 · Windows 中文流水线（等同 ./make-and-publish.sh）
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
Set-Location $Root
$env:ROOT = $Root

. (Join-Path $Root 'scripts\load-dotenv.ps1') -Locale zh

$env:PYTHONPATH = "$Root\src"
$env:AIVIDEO_SOURCE = 'cursor'
$env:AIVIDEO_COMPLIANCE_RELAXED = '1'

$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    $Py = 'python'
}

$DefaultCount = & $Py -c @"
from weekend_edu_topics import is_weekend_edu_mode, weekend_default_count
from cursor_daily_topics import CURSOR_SLOT_ORDER
print(weekend_default_count() if is_weekend_edu_mode() else len(CURSOR_SLOT_ORDER))
"@

if ($RemainingArgs.Count -gt 0 -and $RemainingArgs[0] -match '^--') {
    $Count = $DefaultCount
    $Extra = $RemainingArgs
} elseif ($RemainingArgs.Count -gt 0) {
    $Count = $RemainingArgs[0]
    $Extra = @()
    if ($RemainingArgs.Count -gt 1) { $Extra = $RemainingArgs[1..($RemainingArgs.Count - 1)] }
} else {
    $Count = if ($env:AIVIDEO_MAX_VIDEOS_PER_RUN) { $env:AIVIDEO_MAX_VIDEOS_PER_RUN } else { $DefaultCount }
    $Extra = @()
}

$PyArgs = @((Join-Path $Root 'src\make_publish.py'), '--count', $Count) + $Extra
Write-Host "[make-and-publish] $($PyArgs -join ' ')" -ForegroundColor DarkGray
& $Py @PyArgs
exit $LASTEXITCODE
