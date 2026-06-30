#Requires -Version 5.1
<#
.SYNOPSIS
  Publish to WeChat Channels via PC WeChat UI automation (test).

.EXAMPLE
  .\scripts\publish-shipinhao-pcwechat.ps1
  .\scripts\publish-shipinhao-pcwechat.ps1 -DryRun
  .\scripts\publish-shipinhao-pcwechat.ps1 -Probe
  .\scripts\publish-shipinhao-pcwechat.ps1 -SkipNav
  .\scripts\publish-shipinhao-pcwechat.ps1 -Video archive\published\20260630\zh\xxx.mp4
  .\scripts\publish-shipinhao-pcwechat.ps1 -NoPublish
#>
param(
    [string]$Video = '',
    [ValidateSet('zh', 'en')]
    [string]$Locale = 'zh',
    [string]$Desc = '',
    [switch]$Latest,
    [switch]$DryRun,
    [switch]$NoPublish,
    [switch]$SkipNav,
    [switch]$Probe,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs = @()
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$env:ROOT = $Root

. (Join-Path $Root 'scripts\load-dotenv.ps1') -Locale zh

function Invoke-ExternalQuiet {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$CmdArgs
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @CmdArgs 2>&1 | Out-Null
        if ($null -eq $LASTEXITCODE) { return 0 }
        return [int]$LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

function Invoke-ExternalLogged {
    param(
        [Parameter(Mandatory)][string]$Exe,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$CmdArgs
    )
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @CmdArgs 2>&1 | ForEach-Object { Write-Host $_ }
        if ($null -eq $LASTEXITCODE) { return 0 }
        return [int]$LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
}

$MainPy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $MainPy)) {
    $MainPy = 'python'
}

$pipShowCode = Invoke-ExternalQuiet -Exe $MainPy -CmdArgs @('-m', 'pip', 'show', 'pywinauto')
if ($pipShowCode -ne 0) {
    Write-Host 'Installing requirements-pcwechat.txt ...' -ForegroundColor Yellow
    $req = Join-Path $Root 'requirements-pcwechat.txt'
    $pipInstallCode = Invoke-ExternalLogged -Exe $MainPy -CmdArgs @('-m', 'pip', 'install', '-r', $req)
    if ($pipInstallCode -ne 0) {
        throw 'pip install requirements-pcwechat.txt failed'
    }
}

$env:PYTHONPATH = "$Root\src"

$PyArgs = @("$Root\src\publish_shipinhao_pcwechat.py")
if ($Probe) { $PyArgs += '--probe' }
if ($DryRun) { $PyArgs += '--dry-run' }
if ($NoPublish) { $PyArgs += '--no-publish' }
if ($SkipNav) { $PyArgs += '--skip-nav' }
if ($Locale -and $Locale -ne 'zh') { $PyArgs += '--locale'; $PyArgs += $Locale }
if ($Desc) { $PyArgs += '--desc'; $PyArgs += $Desc }
if ($Video) {
    $PyArgs += '--video'
    $PyArgs += $Video
} elseif ($Latest) {
    $PyArgs += '--latest'
}
if ($ExtraArgs) { $PyArgs += $ExtraArgs }

Write-Host '==> shipinhao PC WeChat UI publish' -ForegroundColor Cyan
$runCode = Invoke-ExternalLogged -Exe $MainPy -CmdArgs $PyArgs
exit $runCode
