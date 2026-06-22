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

function Test-ShipinhaoLogin {
    $SauHome = if ($env:SAU_HOME) { $env:SAU_HOME } else { Join-Path $Root 'vendor\social-auto-upload' }
    $SauPy = Join-Path $SauHome '.venv\Scripts\python.exe'
    if (-not (Test-Path $SauPy)) {
        Write-Host '[make-and-publish] WARN shipinhao needs re-login - SAU Python not found, skip shipinhao auto publish' -ForegroundColor Yellow
        Write-Host '  run first: .\setup-windows.ps1' -ForegroundColor DarkYellow
        return $false
    }

    $Acct = if ($env:SAU_SHIPINHAO_ACCOUNT) { "$($env:SAU_SHIPINHAO_ACCOUNT)".Trim() } else { 'main' }
    $prevPythonPath = $env:PYTHONPATH
    $prevSauHome = $env:SAU_HOME
    $env:PYTHONPATH = if ($prevPythonPath) { "$Root\src;$SauHome;$prevPythonPath" } else { "$Root\src;$SauHome" }
    $env:SAU_HOME = $SauHome

    try {
        Write-Host '[make-and-publish] probing shipinhao login...' -ForegroundColor DarkGray
        & $SauPy "$Root\src\shipinhao_session.py" '--account' $Acct '--quiet'
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    } finally {
        if ($null -ne $prevPythonPath) { $env:PYTHONPATH = $prevPythonPath }
        else { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
        if ($null -ne $prevSauHome) { $env:SAU_HOME = $prevSauHome }
        else { Remove-Item Env:SAU_HOME -ErrorAction SilentlyContinue }
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
    if (Test-ShipinhaoLogin) {
        Write-Host '[make-and-publish] shipinhao login OK, will auto publish' -ForegroundColor DarkGreen
    } else {
        Write-Host '[make-and-publish] WARN shipinhao needs re-login (session expired); skip shipinhao auto publish this run' -ForegroundColor Yellow
        Write-Host '  run: .\scripts\login-cn.ps1 shipinhao' -ForegroundColor DarkYellow
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
