#Requires -Version 5.1
<#
.SYNOPSIS
  国内平台登录 / 校验（Windows）

.EXAMPLE
  .\scripts\login-cn.ps1 bilibili
  .\scripts\login-cn.ps1 douyin --force
  .\scripts\login-cn.ps1 xiaohongshu --check
  .\scripts\login-cn.ps1 shipinhao
  .\scripts\login-cn.ps1 eastmoney
  .\scripts\login-cn.ps1 xueqiu
  .\scripts\login-cn.ps1 zhihu
#>
param(
    [Parameter(Mandatory, Position = 0)]
    [ValidateSet('douyin', 'bilibili', 'xiaohongshu', 'xhs', 'shipinhao', 'eastmoney', 'xueqiu', 'zhihu')]
    [string]$Platform,

    [switch]$Check,
    [switch]$Force,
    [string]$Account = ''
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $Root
$env:ROOT = $Root

. (Join-Path $Root 'scripts\load-dotenv.ps1') -Locale zh

$SauHome = if ($env:SAU_HOME) { $env:SAU_HOME } else { Join-Path $Root 'vendor\social-auto-upload' }
$SauPy = Join-Path $SauHome '.venv\Scripts\python.exe'
if (-not (Test-Path $SauPy)) {
    throw "未找到 SAU Python: $SauPy`n请先运行: .\setup-windows.ps1"
}
$MainPy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $MainPy)) { $MainPy = 'python' }

function Get-OrDefault([string]$Value, [string]$Default) {
    if ($Value) { return $Value }
    return $Default
}

$env:PYTHONPATH = "$Root\src;$SauHome"
$env:SAU_HOME = $SauHome

function Invoke-SessionCheck {
    param([string[]]$Cmd)
    & @Cmd
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

switch ($Platform) {
    'xhs' { $Platform = 'xiaohongshu' }
}

switch ($Platform) {
    'douyin' {
        $Acct = Get-OrDefault $Account (Get-OrDefault $env:SAU_DOUYIN_ACCOUNT 'main')
        if ($Check) {
            Invoke-SessionCheck @($SauPy, "$Root\src\douyin_session.py", '--account', $Acct)
            exit 0
        }
        if ($Force) {
            $cookie = Join-Path $SauHome "cookies\douyin_$Acct.json"
            $profile = Join-Path $SauHome "cookies\browser_profiles\douyin_$Acct"
            Remove-Item $cookie, $profile -Recurse -Force -ErrorAction SilentlyContinue
        }
        & $MainPy "$Root\src\apply_sau_patches.py"
        & $SauPy "$Root\src\douyin_login.py" --login --account $Acct
        Write-Host "登录完成，正在校验…"
        Invoke-SessionCheck @($SauPy, "$Root\src\douyin_session.py", '--account', $Acct)
    }
    'bilibili' {
        $Acct = Get-OrDefault $Account (Get-OrDefault $env:SAU_BILIBILI_ACCOUNT 'main')
        $SauBin = Join-Path $SauHome '.venv\Scripts\sau.exe'
        if (-not (Test-Path $SauBin)) { throw "未找到 sau: $SauBin" }
        Push-Location $SauHome
        if ($Check) {
            & $SauBin bilibili check --account $Acct
            exit $LASTEXITCODE
        }
        if ($Force) {
            Remove-Item "cookies\bilibili_$Acct.json" -Force -ErrorAction SilentlyContinue
        }
        & $SauBin bilibili login --account $Acct
        Pop-Location
    }
    'xiaohongshu' {
        $Acct = Get-OrDefault $Account (Get-OrDefault $env:SAU_XHS_ACCOUNT 'main')
        $env:SAU_XHS_ACCOUNT = $Acct
        $cookie = Join-Path $SauHome "cookies\xiaohongshu_$Acct.json"
        $profile = Join-Path $SauHome "cookies\browser_profiles\xiaohongshu_$Acct"
        if ($Check) {
            & $SauPy "$Root\src\social_publisher.py" xiaohongshu check
            exit $LASTEXITCODE
        }
        if ($Force) {
            Remove-Item $cookie, $profile -Recurse -Force -ErrorAction SilentlyContinue
        }
        Write-Host "即将打开 Chrome 扫码登录小红书…"
        & $SauPy "$Root\src\social_publisher.py" xiaohongshu login
    }
    'shipinhao' {
        $Acct = Get-OrDefault $Account (Get-OrDefault $env:SAU_SHIPINHAO_ACCOUNT 'main')
        if ($Check) {
            Invoke-SessionCheck @($SauPy, "$Root\src\shipinhao_session.py", '--account', $Acct)
            exit 0
        }
        if ($Force) {
            $cookie = Join-Path $SauHome "cookies\tencent_$Acct.json"
            $profile = Join-Path $SauHome "cookies\browser_profiles\tencent_$Acct"
            Remove-Item $cookie, $profile -Recurse -Force -ErrorAction SilentlyContinue
        }
        & $SauPy "$Root\src\shipinhao_login.py" --login --account $Acct
    }
    'eastmoney' {
        $Acct = Get-OrDefault $Account (Get-OrDefault $env:EASTMONEY_ACCOUNT 'main')
        if ($Check) {
            Invoke-SessionCheck @($SauPy, "$Root\src\eastmoney_session.py", '--account', $Acct)
            exit 0
        }
        if ($Force) {
            Remove-Item (Join-Path $SauHome "cookies\eastmoney_$Acct.json") -Force -ErrorAction SilentlyContinue
        }
        & $SauPy "$Root\src\eastmoney_session.py" --login --account $Acct
        Invoke-SessionCheck @($SauPy, "$Root\src\eastmoney_session.py", '--account', $Acct)
    }
    'xueqiu' {
        $Acct = Get-OrDefault $Account (Get-OrDefault $env:XUEQIU_ACCOUNT 'main')
        if ($Check) {
            Invoke-SessionCheck @($SauPy, "$Root\src\xueqiu_session.py", '--account', $Acct)
            exit 0
        }
        if ($Force) {
            Remove-Item (Join-Path $SauHome "cookies\xueqiu_$Acct.json") -Force -ErrorAction SilentlyContinue
        }
        & $SauPy "$Root\src\xueqiu_session.py" --login --account $Acct
        Invoke-SessionCheck @($SauPy, "$Root\src\xueqiu_session.py", '--account', $Acct)
    }
    'zhihu' {
        $Acct = Get-OrDefault $Account (Get-OrDefault $env:ZHIHU_ACCOUNT 'main')
        if ($Check) {
            Invoke-SessionCheck @($SauPy, "$Root\src\zhihu_session.py", '--account', $Acct)
            exit 0
        }
        if ($Force) {
            Remove-Item (Join-Path $SauHome "cookies\zhihu_$Acct.json") -Force -ErrorAction SilentlyContinue
        }
        & $SauPy "$Root\src\zhihu_session.py" --login --account $Acct
        Invoke-SessionCheck @($SauPy, "$Root\src\zhihu_session.py", '--account', $Acct)
    }
}

Write-Host "完成: $Platform" -ForegroundColor Green
