#Requires -Version 5.1
<#
.SYNOPSIS
  AIVideo Windows setup (make-and-publish zh pipeline)

.EXAMPLE
  .\setup-windows.ps1
  .\setup-windows.ps1 -SkipChromium
  .\setup-windows.ps1 -UseWinget
#>
param(
    [switch]$SkipChromium,
    [switch]$SkipSau,
    [switch]$UseWinget
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
Set-Location $Root

function Write-Step([string]$Msg) {
    Write-Host ""
    Write-Host "==> $Msg" -ForegroundColor Cyan
}

function Test-CommandExists([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Find-Python312 {
    $candidates = @(
        { & py -3.12 -c 'import sys; print(sys.executable)' 2>$null },
        { & py -3.11 -c 'import sys; print(sys.executable)' 2>$null },
        { & py -3.10 -c 'import sys; print(sys.executable)' 2>$null },
        { & python -c 'import sys; print(sys.executable)' 2>$null }
    )
    foreach ($try in $candidates) {
        try {
            $path = (& $try).Trim()
            if (-not $path) { continue }
            $ver = & $path -c 'import sys; print(str(sys.version_info.major) + chr(46) + str(sys.version_info.minor))'
            $parts = $ver.Split('.')
            $minor = [int]$parts[1]
            if ([int]$parts[0] -eq 3 -and $minor -ge 10 -and $minor -le 12) {
                return $path
            }
        } catch { }
    }
    return $null
}

function Install-WithWinget([string]$Id, [string]$Label) {
    if (-not (Test-CommandExists 'winget')) {
        Write-Host "  winget not found, install manually: $Label" -ForegroundColor Yellow
        return $false
    }
    Write-Host "  winget install $Label ..."
    & winget install --id $Id -e --accept-source-agreements --accept-package-agreements
    return $true
}

Write-Step "AIVideo Windows setup - $Root"

Write-Step "Check Git"
if (-not (Test-CommandExists 'git')) {
    if ($UseWinget) { Install-WithWinget 'Git.Git' 'Git' | Out-Null }
    if (-not (Test-CommandExists 'git')) {
        throw "Git not found. Install Git for Windows or rerun with -UseWinget"
    }
}

Write-Step "Check Python 3.10-3.12"
$PythonExe = Find-Python312
if (-not $PythonExe) {
    if ($UseWinget) {
        Install-WithWinget 'Python.Python.3.12' 'Python 3.12' | Out-Null
        $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [System.Environment]::GetEnvironmentVariable('Path', 'User')
        $PythonExe = Find-Python312
    }
    if (-not $PythonExe) {
        throw "Need Python 3.10-3.12 (SAU does not support 3.13+). Install from python.org"
    }
}
Write-Host ('  Python = ' + $PythonExe)

Write-Step "Check ffmpeg"
if (-not (Test-CommandExists 'ffmpeg')) {
    if ($UseWinget) { Install-WithWinget 'Gyan.FFmpeg' 'FFmpeg' | Out-Null }
    if (-not (Test-CommandExists 'ffmpeg')) {
        Write-Host "  WARN: ffmpeg not found. Video compose will fail." -ForegroundColor Yellow
    }
} else {
    Write-Host ('  ffmpeg = ' + (ffmpeg -version | Select-Object -First 1))
}

Write-Step "Check Google Chrome"
$ChromePaths = @(
    "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$ChromeExe = $ChromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $ChromeExe) {
    if ($UseWinget) { Install-WithWinget 'Google.Chrome' 'Google Chrome' | Out-Null }
    $ChromeExe = $ChromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($ChromeExe) {
    Write-Host ('  Chrome = ' + $ChromeExe)
} else {
    Write-Host "  WARN: Chrome not found. Browser login/publish will fail." -ForegroundColor Yellow
}

Write-Step "Config .env"
$EnvFile = Join-Path $Root '.env'
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root '.env.example') $EnvFile
    Write-Host "  Created .env from .env.example - edit API keys and publish flags"
} else {
    Write-Host "  .env already exists, skipped"
}

if ($ChromeExe -and (Test-Path $EnvFile)) {
    $envText = Get-Content $EnvFile -Raw -Encoding UTF8
    if ($envText -notmatch '(?m)^LOCAL_CHROME_PATH=') {
        $chromePath = $ChromeExe -replace '\\', '/'
        Add-Content -Path $EnvFile -Encoding UTF8 -Value "`nLOCAL_CHROME_PATH=$chromePath"
        Write-Host "  Wrote LOCAL_CHROME_PATH"
    }
}

Write-Step "Main venv .venv"
$MainVenv = Join-Path $Root '.venv'
$MainPy = Join-Path $MainVenv 'Scripts\python.exe'
if (-not (Test-Path $MainPy)) {
    & $PythonExe -m venv $MainVenv
}
$PipMirror = if ($env:PYPI_MIRROR) { $env:PYPI_MIRROR } else { 'https://mirrors.aliyun.com/pypi/simple/' }
$PipHost = if ($env:PYPI_TRUSTED_HOST) { $env:PYPI_TRUSTED_HOST } else { 'mirrors.aliyun.com' }
& $MainPy -m pip install -U pip -i $PipMirror --trusted-host $PipHost
& $MainPy -m pip install -r (Join-Path $Root 'requirements.txt') -i $PipMirror --trusted-host $PipHost
Write-Host ('  Main venv = ' + $MainPy)

if (-not $SkipSau) {
    Write-Step "social-auto-upload (vendor)"
    $SauHome = if ($env:SAU_HOME) { $env:SAU_HOME } else { Join-Path $Root 'vendor\social-auto-upload' }
    $SauRepo = if ($env:SAU_REPO) { $env:SAU_REPO } else { 'https://github.com/dreammis/social-auto-upload.git' }

    if (-not (Test-Path (Join-Path $SauHome '.git'))) {
        New-Item -ItemType Directory -Force -Path (Split-Path $SauHome) | Out-Null
        git clone --depth 1 $SauRepo $SauHome
    } else {
        Push-Location $SauHome
        git pull --ff-only 2>$null
        Pop-Location
    }

    $SauPy = Join-Path $SauHome '.venv\Scripts\python.exe'
    if (-not (Test-Path $SauPy)) {
        & $PythonExe -m venv (Join-Path $SauHome '.venv')
    }
    & $SauPy -m pip install -U pip -i $PipMirror --trusted-host $PipHost
    Push-Location $SauHome
    & $SauPy -m pip install -e . -i $PipMirror --trusted-host $PipHost
    & $SauPy -m pip install patchright -i $PipMirror --trusted-host $PipHost
    Pop-Location

    $greenletOk = $false
    try {
        & $SauPy -c "import greenlet; from patchright.async_api import async_playwright" 2>$null
        $greenletOk = ($LASTEXITCODE -eq 0)
    } catch { }
    if (-not $greenletOk) {
        Write-Host "  WARN: greenlet import failed, reinstalling..." -ForegroundColor Yellow
        & $SauPy -m pip install --force-reinstall --no-cache-dir greenlet -i $PipMirror --trusted-host $PipHost
        & $SauPy -c "import greenlet; from patchright.async_api import async_playwright" 2>$null
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  WARN: greenlet still broken. Run: .\scripts\repair-sau-venv.ps1" -ForegroundColor Yellow
            Write-Host "  Or install VC++: winget install Microsoft.VCRedist.2015+.x64" -ForegroundColor Yellow
        }
    }

    $Conf = Join-Path $SauHome 'conf.py'
    if (-not (Test-Path $Conf)) {
        Copy-Item (Join-Path $SauHome 'conf.example.py') $Conf
    }
    if ($ChromeExe -and (Test-Path $Conf)) {
        $confText = Get-Content $Conf -Raw -Encoding UTF8
        $chromeEsc = $ChromeExe -replace '\\', '/'
        if ($confText -notmatch 'LOCAL_CHROME_PATH') {
            Add-Content $Conf "LOCAL_CHROME_PATH = `"$chromeEsc`""
        }
    }

    $env:PYTHONPATH = "$Root\src"
    & $MainPy (Join-Path $Root 'src\apply_sau_patches.py')

    if (-not $SkipChromium) {
        if ($ChromeExe) {
            Write-Host "  Skip patchright chromium (using installed Chrome via channel=chrome)"
        } else {
            Write-Step "Install Chromium (patchright)"
            Write-Host "  WARN: No Chrome found. Direct download may be slow; install Google Chrome first." -ForegroundColor Yellow
            Push-Location $SauHome
            Remove-Item Env:PLAYWRIGHT_DOWNLOAD_HOST -ErrorAction SilentlyContinue
            & $SauPy -m patchright install chromium
            Pop-Location
        }
    }
    Write-Host ('  SAU = ' + $SauHome)
}

@('logs\zh', 'logs\en', 'output\zh', 'output\en', 'archive\published') | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root $_) | Out-Null
}

Write-Step "Smoke test"
$env:PYTHONPATH = "$Root\src"
& $MainPy -c 'from paths import ROOT; from invoke_script import main_python; print("OK", ROOT, main_python())'
if (-not $SkipSau) {
    & $MainPy -c 'from sau_client import resolve_sau_bin; print("sau", resolve_sau_bin())'
}

Write-Step "Done"
Write-Host ""
Write-Host "Next steps (see docs\WINDOWS-DEPLOY.md):"
Write-Host "  1. Edit .env - API keys + AIVIDEO_PUBLISH_* in section zh"
Write-Host "  2. Login cookies (RDP desktop, scan QR):"
Write-Host "       .\scripts\login-cn.ps1 bilibili"
Write-Host "       .\scripts\login-cn.ps1 douyin"
Write-Host "       .\scripts\login-cn.ps1 xiaohongshu"
Write-Host "       .\scripts\login-cn.ps1 shipinhao"
Write-Host "       .\scripts\login-cn.ps1 eastmoney"
Write-Host "       .\scripts\login-cn.ps1 xueqiu"
Write-Host "  3. Test run: .\make-and-publish.ps1 1"
Write-Host "  4. Schedule:  .\register-daily-publish.ps1 -At 08:00"
Write-Host ""
