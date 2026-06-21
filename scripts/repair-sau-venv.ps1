#Requires -Version 5.1
<#
.SYNOPSIS
  修复 SAU venv 中 greenlet / patchright 导入失败（Windows DLL load failed）

.EXAMPLE
  .\scripts\repair-sau-venv.ps1
  .\scripts\repair-sau-venv.ps1 -RecreateVenv
#>
param(
    [switch]$RecreateVenv
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root

$SauHome = if ($env:SAU_HOME) { $env:SAU_HOME } else { Join-Path $Root 'vendor\social-auto-upload' }
$SauPy = Join-Path $SauHome '.venv\Scripts\python.exe'
$MainPy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $MainPy)) { $MainPy = 'python' }

$PipMirror = if ($env:PYPI_MIRROR) { $env:PYPI_MIRROR } else { 'https://mirrors.aliyun.com/pypi/simple/' }
$PipHost = if ($env:PYPI_TRUSTED_HOST) { $env:PYPI_TRUSTED_HOST } else { 'mirrors.aliyun.com' }

function Test-SauImports {
    param([string]$Py)
    & $Py -c "import greenlet; from patchright.async_api import async_playwright; print('greenlet OK', greenlet.__version__)"
    return $LASTEXITCODE -eq 0
}

function Get-SystemPython {
    foreach ($cmd in @(
        { & py -3.12 -c 'import sys; print(sys.executable)' 2>$null },
        { & py -3.11 -c 'import sys; print(sys.executable)' 2>$null },
        { & py -3.10 -c 'import sys; print(sys.executable)' 2>$null },
        { & python -c 'import sys; print(sys.executable)' 2>$null }
    )) {
        try {
            $path = (& $cmd).Trim()
            if ($path -and (Test-Path $path)) { return $path }
        } catch { }
    }
    return $null
}

if ($RecreateVenv) {
    $SystemPy = Get-SystemPython
    if (-not $SystemPy) {
        throw "未找到 Python 3.10-3.12，无法重建 SAU venv"
    }
    Write-Host "==> 删除并重建 SAU venv ($SystemPy)" -ForegroundColor Cyan
    Remove-Item -Recurse -Force (Join-Path $SauHome '.venv') -ErrorAction SilentlyContinue
    & $SystemPy -m venv (Join-Path $SauHome '.venv')
    $SauPy = Join-Path $SauHome '.venv\Scripts\python.exe'
    & $SauPy -m pip install -U pip -i $PipMirror --trusted-host $PipHost
    Push-Location $SauHome
    & $SauPy -m pip install -e . -i $PipMirror --trusted-host $PipHost
    & $SauPy -m pip install patchright -i $PipMirror --trusted-host $PipHost
    Pop-Location
    $env:PYTHONPATH = "$Root\src"
    & $MainPy (Join-Path $Root 'src\apply_sau_patches.py')
}

if (-not (Test-Path $SauPy)) {
    throw "未找到 SAU Python: $SauPy`n请先运行: .\setup-windows.ps1"
}

Write-Host "==> 检测 greenlet / patchright" -ForegroundColor Cyan
if (Test-SauImports $SauPy) {
    Write-Host "SAU venv 正常，无需修复" -ForegroundColor Green
    exit 0
}

Write-Host "greenlet 导入失败，尝试强制重装..." -ForegroundColor Yellow
& $SauPy -m pip install --force-reinstall --no-cache-dir greenlet -i $PipMirror --trusted-host $PipHost
if (Test-SauImports $SauPy) {
    Write-Host "修复成功" -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Host "仍失败。Windows Server 精简镜像常缺 VC++ 运行库，请安装后重试：" -ForegroundColor Yellow
Write-Host "  winget install --id Microsoft.VCRedist.2015+.x64 -e --accept-source-agreements --accept-package-agreements"
Write-Host ""
Write-Host "安装 VC++ 后执行："
Write-Host "  .\scripts\repair-sau-venv.ps1"
Write-Host ""
Write-Host "若仍不行，重建 venv："
Write-Host "  .\scripts\repair-sau-venv.ps1 -RecreateVenv"
exit 1
