# Load env vars from .env (shared + zh). Windows daily pipeline.
# Usage: . .\scripts\load-dotenv.ps1
param(
    [string]$Locale = 'zh'
)

$ErrorActionPreference = 'Stop'

if (-not $env:ROOT) {
    $script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
} else {
    $script:Root = $env:ROOT
}

function Apply-EnvLine {
    param(
        [string]$Line,
        [bool]$Force
    )
    $Line = $Line.TrimEnd("`r")
    if (-not $Line -or $Line -match '^\s*#') { return }
    if ($Line -notmatch '=') { return }
    $key = ($Line -split '=', 2)[0].Trim()
    $val = ($Line -split '=', 2)[1].Trim()
    if ($val -match '^"(.*)"$') { $val = $Matches[1] }
    elseif ($val -match "^'(.*)'$") { $val = $Matches[1] }
    if ($Force -or -not (Test-Path "Env:$key")) {
        Set-Item -Path "Env:$key" -Value $val
    }
}

function Load-EnvSections {
    param(
        [string]$FilePath,
        [string]$WantLocale,
        [bool]$LocaleForce = $true
    )
    if (-not (Test-Path $FilePath)) { return }
    $section = 'shared'
    # Windows PowerShell 5.1 的 Get-Content -Encoding UTF8 对无 BOM 文件偶发按系统 ANSI(GBK) 读，
    # 会把「AI财知道」弄成「AI璐…」烧进画面。显式用无 BOM UTF-8。
    $utf8 = New-Object System.Text.UTF8Encoding $false
    $lines = [System.IO.File]::ReadAllLines((Resolve-Path -LiteralPath $FilePath).Path, $utf8)
    foreach ($line in $lines) {
        if ($line -match '^#==\s*section:\s*(\w+)\s*==') {
            $section = $Matches[1].ToLower()
            continue
        }
        switch ($section) {
            'shared' { Apply-EnvLine -Line $line -Force $false }
            { $_ -eq $WantLocale } { Apply-EnvLine -Line $line -Force $LocaleForce }
            default { }
        }
    }
}

function Read-EnvMap {
    param([string]$FilePath, [string]$WantLocale)
    $map = @{}
    if (-not (Test-Path $FilePath)) { return $map }
    $section = 'shared'
    $utf8 = New-Object System.Text.UTF8Encoding $false
    foreach ($line in [System.IO.File]::ReadAllLines((Resolve-Path -LiteralPath $FilePath).Path, $utf8)) {
        if ($line -match '^#==\s*section:\s*(\w+)\s*==') { $section = $Matches[1].ToLower(); continue }
        $line = $line.TrimEnd("`r")
        if (-not $line -or $line -match '^\s*#' -or $line -notmatch '=') { continue }
        $key = ($line -split '=', 2)[0].Trim()
        $val = ($line -split '=', 2)[1].Trim()
        if ($val -match '^"(.*)"$') { $val = $Matches[1] } elseif ($val -match "^'(.*)'$") { $val = $Matches[1] }
        if ($section -eq $WantLocale -or ($section -eq 'shared' -and -not $map.ContainsKey($key))) { $map[$key] = $val }
    }
    return $map
}

# config\defaults.env follows git: its keys win over stale .env values; .env only adds secrets/accounts/paths
function Apply-CodeDefaults {
    param([string]$WantLocale)
    $fromEnv = Read-EnvMap -FilePath (Join-Path $Root '.env') -WantLocale $WantLocale
    $defs = Read-EnvMap -FilePath (Join-Path $Root 'config\defaults.env') -WantLocale $WantLocale
    foreach ($key in $defs.Keys) {
        $cur = [Environment]::GetEnvironmentVariable($key, 'Process')
        if ($null -eq $cur -or ($fromEnv.ContainsKey($key) -and $fromEnv[$key] -eq $cur)) {
            Set-Item -Path "Env:$key" -Value $defs[$key]
        }
    }
}

foreach ($key in @(
    'AIVIDEO_BRAND_NAME', 'AIVIDEO_BRAND_TAGLINE',
    'AIVIDEO_OUTRO_HEADLINE', 'AIVIDEO_OUTRO_SUBLINE',
    'AIVIDEO_OUTRO_NARRATION', 'AIVIDEO_OUTRO_NARRATION_VARIANTS',
    'VOLCENGINE_TTS_SPEAKER', 'VOLCENGINE_TTS_RESOURCE_ID', 'VOLCENGINE_TTS_MODEL',
    'VOLCENGINE_TTS_SPEECH_RATE', 'VOLCENGINE_TTS_ATEMPO', 'VOLCENGINE_TTS_EMOTION'
)) {
    Remove-Item -Path "Env:$key" -ErrorAction SilentlyContinue
}

Load-EnvSections -FilePath (Join-Path $Root '.env') -WantLocale $Locale
# 先补齐，再让代码配置覆盖 .env 里的旧值
Load-EnvSections -FilePath (Join-Path $Root 'config\defaults.env') -WantLocale $Locale -LocaleForce $false
Apply-CodeDefaults -WantLocale $Locale
$env:AIVIDEO_LOCALE = $Locale
$env:ROOT = $Root
