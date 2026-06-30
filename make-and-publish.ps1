#Requires -Version 5.1
<#
.SYNOPSIS
  AI Cai Zhi Dao - Windows zh pipeline main entry (preferred for daily auto publish).

.DESCRIPTION
  Weekday: 5 news slots (default 5).
  Weekend: Opus dynamic edu topics (default 3, dedup from history).

  NOTE: keep this file ASCII-only. Windows PowerShell 5.1 reads .ps1 as the
  system ANSI codepage (e.g. GBK); non-ASCII chars here can break parsing.
  Any Chinese banner text comes from Python (UTF-8) at runtime instead.

.EXAMPLE
  .\make-and-publish.ps1
  .\make-and-publish.ps1 1
  .\make-and-publish.ps1 --no-publish
  .\make-and-publish.ps1 --slot edu_quant
#>
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
Set-Location $Root
$env:ROOT = $Root

. (Join-Path $Root 'scripts\load-dotenv.ps1') -Locale zh

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$Root\src;$env:PYTHONPATH" } else { "$Root\src" }
$env:AIVIDEO_SOURCE = 'cursor'
$env:AIVIDEO_COMPLIANCE_RELAXED = '1'
# Force Python to emit UTF-8 so emoji/Chinese in logs do not crash on Windows GBK console.
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    $Py = 'python'
}

function Test-EnvEnabled {
    param(
        [Parameter(Mandatory)][string]$Name,
        [string]$Default = '0'
    )
    $item = Get-Item -Path "Env:$Name" -ErrorAction SilentlyContinue
    $val = if ($item -and $item.Value) { "$($item.Value)".Trim() } else { $Default }
    return $val -match '^(1|true|yes|on)$'
}

function Test-ShipinhaoReady {
    # PC WeChat UI automation: requires pywinauto installed and WeChat running
    # (logged in, "auto login this device" on). No browser/SAU session needed.
    $wechat = Get-Process -Name 'WeChat', 'Weixin' -ErrorAction SilentlyContinue
    if (-not $wechat) {
        Write-Host '[make-and-publish] WARN PC WeChat not running; skip shipinhao auto publish' -ForegroundColor Yellow
        Write-Host '  open WeChat, log in (keep auto-login on), then rerun' -ForegroundColor DarkYellow
        return $false
    }

    $prevPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = if ($prevPythonPath) { "$Root\src;$prevPythonPath" } else { "$Root\src" }
    try {
        & $Py '-c' 'import pywinauto' 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host '[make-and-publish] WARN pywinauto missing; skip shipinhao auto publish' -ForegroundColor Yellow
            Write-Host '  run: .\setup-windows.ps1  (installs requirements-pcwechat.txt)' -ForegroundColor DarkYellow
            return $false
        }
        return $true
    } catch {
        return $false
    } finally {
        if ($null -ne $prevPythonPath) { $env:PYTHONPATH = $prevPythonPath }
        else { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
    }
}

$ModeInfo = & $Py (Join-Path $Root 'src\print_publish_mode.py')
# python may print extra lines (warnings); keep only the last line containing a pipe
$ModeLine = @($ModeInfo | Where-Object { $_ -and ($_ -like '*|*') } | Select-Object -Last 1)
if (-not $ModeLine) { throw "cannot parse publish mode: $ModeInfo" }
$ModeParts = ([string]$ModeLine[0]).Split('|', 3)
$DefaultCount = $ModeParts[1]
Write-Host "[make-and-publish] $($ModeParts[2])" -ForegroundColor Cyan

if ($RemainingArgs.Count -gt 0 -and $RemainingArgs[0] -like '--*') {
    $Count = $DefaultCount
    $Extra = @($RemainingArgs)
} elseif ($RemainingArgs.Count -gt 0) {
    $Count = [string]$RemainingArgs[0]
    $Extra = @()
    if ($RemainingArgs.Count -gt 1) {
        $Extra = @($RemainingArgs | Select-Object -Skip 1)
    }
} else {
    $Count = if ($env:AIVIDEO_MAX_VIDEOS_PER_RUN) { [string]$env:AIVIDEO_MAX_VIDEOS_PER_RUN } else { [string]$DefaultCount }
    $Extra = @()
}

$ScriptPath = Join-Path $Root 'src\make_publish.py'
$ArgList = @($ScriptPath, '--count', $Count)
foreach ($a in $Extra) {
    if ($null -ne $a -and "$a".Length -gt 0) {
        $ArgList += [string]$a
    }
}
Write-Host "[make-and-publish] $($ArgList -join ' ')" -ForegroundColor DarkGray

$WillPublish = -not ($Extra | Where-Object { $_ -eq '--no-publish' })
if ($WillPublish -and (Test-EnvEnabled -Name 'AIVIDEO_PUBLISH_SHIPINHAO')) {
    if (Test-ShipinhaoReady) {
        Write-Host '[make-and-publish] PC WeChat ready, will auto publish shipinhao' -ForegroundColor DarkGreen
    } else {
        Write-Host '[make-and-publish] skip shipinhao auto publish this run' -ForegroundColor Yellow
        $env:AIVIDEO_PUBLISH_SHIPINHAO = '0'
    }
}

# PS 5.1 `& python @args` array splat can falsely report "index out of bounds"
# and exit early under ErrorActionPreference=Stop while python keeps running.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    if ($Extra.Count -gt 0) {
        & $Py $ScriptPath '--count' $Count @Extra
    } else {
        & $Py $ScriptPath '--count' $Count
    }
    $ExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
} finally {
    $ErrorActionPreference = $prevEap
}
exit $ExitCode
