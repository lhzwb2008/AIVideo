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

# .env holds only secrets and machine-specific values; everything else comes from config\defaults.env
$script:EnvFileAllowed = '(_API_KEY|_KEY_ID|_KEY_SECRET|_SECRET|_TOKEN|_PASSWORD)$|^(CURSOR_SANDBOX_REPO_URL|CURSOR_SANDBOX_REPO_REF|SAU_BIN|SAU_HOME|FFMPEG_PATH|LOCAL_CHROME_PATH|OSS_BUCKET|OSS_PREFIX|OSS_REGION|OSS_ENDPOINT|WECHAT_APP_ID)$'

function Apply-EnvLine {
    param(
        [string]$Line,
        [bool]$Force,
        [bool]$SecretsOnly = $false
    )
    $Line = $Line.TrimEnd("`r")
    if (-not $Line -or $Line -match '^\s*#') { return }
    if ($Line -notmatch '=') { return }
    $key = ($Line -split '=', 2)[0].Trim()
    $val = ($Line -split '=', 2)[1].Trim()
    if ($val -match '^"(.*)"$') { $val = $Matches[1] }
    elseif ($val -match "^'(.*)'$") { $val = $Matches[1] }
    if ($SecretsOnly -and $key -notmatch $script:EnvFileAllowed) { return }
    if ($Force -or -not (Test-Path "Env:$key")) {
        Set-Item -Path "Env:$key" -Value $val
    }
}

function Load-EnvSections {
    param(
        [string]$FilePath,
        [string]$WantLocale,
        [bool]$LocaleForce = $true,
        [bool]$SecretsOnly = $false
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
            'shared' { Apply-EnvLine -Line $line -Force $false -SecretsOnly $SecretsOnly }
            { $_ -eq $WantLocale } { Apply-EnvLine -Line $line -Force $LocaleForce -SecretsOnly $SecretsOnly }
            default { }
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

Load-EnvSections -FilePath (Join-Path $Root '.env') -WantLocale $Locale -SecretsOnly $true
# 其余配置都来自这里（.env 只提供密钥）
Load-EnvSections -FilePath (Join-Path $Root 'config\defaults.env') -WantLocale $Locale -LocaleForce $false
$env:AIVIDEO_LOCALE = $Locale
$env:ROOT = $Root
