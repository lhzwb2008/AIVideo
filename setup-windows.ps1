#Requires -Version 5.1
<#
.SYNOPSIS
  AIVideo Windows 服务器一键安装（中文 make-and-publish 流水线）

.DESCRIPTION
  安装：Git 检测、Python 3.10–3.12 主 venv、social-auto-upload、Chromium、ffmpeg 检测。
  首次运行会从 .env.example 复制 .env（若不存在）。

.EXAMPLE
  .\setup-windows.ps1
  .\setup-windows.ps1 -SkipChromium   # 已装过 patchright 浏览器时可跳过
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
        { & py -3.12 -c "import sys; print(sys.executable)" 2>$null },
        { & py -3.11 -c "import sys; print(sys.executable)" 2>$null },
        { & py -3.10 -c "import sys; print(sys.executable)" 2>$null },
        { & python -c "import sys; print(sys.executable)" 2>$null }
    )
    foreach ($try in $candidates) {
        try {
            $path = (& $try).Trim()
            if (-not $path) { continue }
            $ver = & $path -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
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
        Write-Host "  未找到 winget，请手动安装: $Label" -ForegroundColor Yellow
        return $false
    }
    Write-Host "  winget 安装 $Label ..."
    & winget install --id $Id -e --accept-source-agreements --accept-package-agreements
    return $true
}

Write-Step "AIVideo Windows 安装 · $Root"

# ── 1. 系统依赖 ─────────────────────────────────────────────
Write-Step "检查 Git"
if (-not (Test-CommandExists 'git')) {
    if ($UseWinget) { Install-WithWinget 'Git.Git' 'Git' | Out-Null }
    if (-not (Test-CommandExists 'git')) {
        throw "缺少 Git。请安装 Git for Windows 后重试，或加 -UseWinget"
    }
}

Write-Step "检查 Python 3.10–3.12"
$Python = Find-Python312
if (-not $Python) {
    if ($UseWinget) {
        Install-WithWinget 'Python.Python.3.12' 'Python 3.12' | Out-Null
        $env:Path = [System.Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [System.Environment]::GetEnvironmentVariable('Path', 'User')
        $Python = Find-Python312
    }
    if (-not $Python) {
        throw "需要 Python 3.10–3.12（SAU 不支持 3.13+）。请从 python.org 安装后重试。"
    }
}
Write-Host "  Python: $Python"

Write-Step "检查 ffmpeg"
if (-not (Test-CommandExists 'ffmpeg')) {
    if ($UseWinget) { Install-WithWinget 'Gyan.FFmpeg' 'FFmpeg' | Out-Null }
    if (-not (Test-CommandExists 'ffmpeg')) {
        Write-Host "  警告: 未找到 ffmpeg，视频合成会失败。请安装后把 ffmpeg 加入 PATH。" -ForegroundColor Yellow
    }
} else {
    Write-Host "  ffmpeg: $(ffmpeg -version | Select-Object -First 1)"
}

Write-Step "检查 Google Chrome"
$ChromePaths = @(
    "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe"
)
$Chrome = $ChromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Chrome) {
    if ($UseWinget) { Install-WithWinget 'Google.Chrome' 'Google Chrome' | Out-Null }
    $Chrome = $ChromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if ($Chrome) {
    Write-Host "  Chrome: $Chrome"
} else {
    Write-Host "  警告: 未找到 Chrome，浏览器登录/发布会失败。" -ForegroundColor Yellow
}

# ── 2. .env ─────────────────────────────────────────────────
Write-Step "配置文件"
$EnvFile = Join-Path $Root '.env'
if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root '.env.example') $EnvFile
    Write-Host "  已从 .env.example 创建 .env — 请编辑 API 密钥与发布开关"
} else {
    Write-Host "  已存在 .env，跳过复制"
}

if ($Chrome -and (Test-Path $EnvFile)) {
    $envText = Get-Content $EnvFile -Raw -Encoding UTF8
    if ($envText -notmatch '(?m)^LOCAL_CHROME_PATH=') {
        Add-Content -Path $EnvFile -Encoding UTF8 -Value "`nLOCAL_CHROME_PATH=$($Chrome -replace '\\','/')"
        Write-Host "  已写入 LOCAL_CHROME_PATH"
    }
}

# ── 3. 主项目 venv ──────────────────────────────────────────
Write-Step "主项目虚拟环境 .venv"
$MainVenv = Join-Path $Root '.venv'
$MainPy = Join-Path $MainVenv 'Scripts\python.exe'
if (-not (Test-Path $MainPy)) {
    & $Python -m venv $MainVenv
}
$PipMirror = if ($env:PYPI_MIRROR) { $env:PYPI_MIRROR } else { 'https://mirrors.aliyun.com/pypi/simple/' }
$PipHost = if ($env:PYPI_TRUSTED_HOST) { $env:PYPI_TRUSTED_HOST } else { 'mirrors.aliyun.com' }
& $MainPy -m pip install -U pip -i $PipMirror --trusted-host $PipHost
& $MainPy -m pip install -r (Join-Path $Root 'requirements.txt') -i $PipMirror --trusted-host $PipHost
Write-Host "  主 venv: $MainPy"

# ── 4. social-auto-upload ───────────────────────────────────
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
        & $Python -m venv (Join-Path $SauHome '.venv')
    }
    & $SauPy -m pip install -U pip -i $PipMirror --trusted-host $PipHost
    Push-Location $SauHome
    & $SauPy -m pip install -e . -i $PipMirror --trusted-host $PipHost
    Pop-Location

    $Conf = Join-Path $SauHome 'conf.py'
    if (-not (Test-Path $Conf)) {
        Copy-Item (Join-Path $SauHome 'conf.example.py') $Conf
    }
    if ($Chrome -and (Test-Path $Conf)) {
        $confText = Get-Content $Conf -Raw -Encoding UTF8
        $chromeEsc = $Chrome -replace '\\', '/'
        if ($confText -notmatch 'LOCAL_CHROME_PATH') {
            Add-Content $Conf "LOCAL_CHROME_PATH = `"$chromeEsc`""
        }
    }

    $env:PYTHONPATH = "$Root\src"
    & $MainPy (Join-Path $Root 'src\apply_sau_patches.py')

    if (-not $SkipChromium) {
        Write-Step "安装 Chromium (patchright)"
        Push-Location $SauHome
        & $SauPy -m patchright install chromium
        Pop-Location
    }
    Write-Host "  SAU: $SauHome"
}

# ── 5. 目录 ─────────────────────────────────────────────────
@('logs\zh', 'logs\en', 'output\zh', 'output\en', 'archive\published') | ForEach-Object {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root $_) | Out-Null
}

# ── 6. 冒烟测试 ─────────────────────────────────────────────
Write-Step "冒烟测试"
$env:PYTHONPATH = "$Root\src"
& $MainPy -c "from paths import ROOT; from invoke_script import main_python; print('OK', ROOT, main_python())"

Write-Step "安装完成"
Write-Host @"

下一步（详见 docs\WINDOWS-DEPLOY.md）:
  1. 编辑 .env — 填 API 密钥，并在 #== section: zh == 打开发布开关
  2. 登录各平台 cookie（需 RDP 桌面会话扫码）:
       .\scripts\login-cn.ps1 bilibili
       .\scripts\login-cn.ps1 douyin
       .\scripts\login-cn.ps1 xiaohongshu
       .\scripts\login-cn.ps1 shipinhao
       .\scripts\login-cn.ps1 eastmoney
       .\scripts\login-cn.ps1 xueqiu
  3. 试跑: .\make-and-publish.ps1 1
  4. 注册计划任务: .\scripts\register-daily-publish.ps1 -At 08:00

"@
