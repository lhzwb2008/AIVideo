#Requires -Version 5.1
<#
.SYNOPSIS
  修复损坏的 douyin_uploader/main.py（旧补丁误插入 busy_marker 导致 sau 无法启动）

.EXAMPLE
  .\scripts\repair-sau-douyin.ps1
#>
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root

$SauHome = Join-Path $Root 'vendor\social-auto-upload'
$DouyinMain = Join-Path $SauHome 'uploader\douyin_uploader\main.py'
$MainPy = Join-Path $Root '.venv\Scripts\python.exe'

Write-Host "==> 从 SAU git 恢复 douyin main.py" -ForegroundColor Cyan
Push-Location $SauHome
git checkout -- uploader/douyin_uploader/main.py
Pop-Location

Write-Host "==> 重新打补丁" -ForegroundColor Cyan
& $MainPy (Join-Path $Root 'src\apply_sau_patches.py')

Write-Host "==> 验证 sau.exe" -ForegroundColor Cyan
& (Join-Path $SauHome '.venv\Scripts\sau.exe') --help | Select-Object -First 3
Write-Host "修复完成" -ForegroundColor Green
