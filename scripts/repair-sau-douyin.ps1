#Requires -Version 5.1
<#
.SYNOPSIS
  Repair corrupted douyin_uploader/main.py (breaks sau.exe / Bilibili login).

.EXAMPLE
  .\scripts\repair-sau-douyin.ps1
#>
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root

$SauHome = Join-Path $Root 'vendor\social-auto-upload'
$MainPy = Join-Path $Root '.venv\Scripts\python.exe'
$SauExe = Join-Path $SauHome '.venv\Scripts\sau.exe'

Write-Host '==> Restore douyin main.py from SAU git' -ForegroundColor Cyan
Push-Location $SauHome
git checkout -- uploader/douyin_uploader/main.py
if ($LASTEXITCODE -ne 0) {
    Pop-Location
    throw 'git checkout failed - is vendor/social-auto-upload a git repo?'
}
Pop-Location

Write-Host '==> Re-apply SAU patches' -ForegroundColor Cyan
& $MainPy (Join-Path $Root 'src\apply_sau_patches.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host '==> Verify sau.exe' -ForegroundColor Cyan
& $SauExe --help | Select-Object -First 3
if ($LASTEXITCODE -ne 0) {
    throw 'sau.exe still fails - check output above'
}

Write-Host 'Done. You can run: .\scripts\login-cn.ps1 bilibili' -ForegroundColor Green
