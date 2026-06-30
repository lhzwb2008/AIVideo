#Requires -Version 5.1
<#
.SYNOPSIS
  PC 微信客户端 · 视频号 UI 自动化发布（测试）

.DESCRIPTION
  默认发布 archive/published 下最新的 zh 视频。
  需 Windows + PC 微信已登录（建议勾选「自动登录该设备」）。

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

$MainPy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $MainPy)) {
    $MainPy = 'python'
}

# 检查 pywinauto
& $MainPy -c "import pywinauto" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '安装 PC 微信自动化依赖…' -ForegroundColor Yellow
    & $MainPy -m pip install -r (Join-Path $Root 'requirements-pcwechat.txt')
    if ($LASTEXITCODE -ne 0) { throw 'pip install requirements-pcwechat.txt 失败' }
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

Write-Host '==> PC 微信 · 视频号 UI 发布' -ForegroundColor Cyan
& $MainPy @PyArgs
if ($null -ne $LASTEXITCODE) { exit [int]$LASTEXITCODE }
