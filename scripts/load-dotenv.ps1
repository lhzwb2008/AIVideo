# Load env vars from .env by locale section (Windows).
# Usage: . .\scripts\load-dotenv.ps1 zh
param(
    [ValidateSet('zh', 'en')]
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
        [string]$WantLocale
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
            { $_ -eq $WantLocale } { Apply-EnvLine -Line $line -Force $true }
            default { }
        }
    }
}

foreach ($key in @(
    'AIVIDEO_BRAND_NAME', 'AIVIDEO_BRAND_TAGLINE',
    'AIVIDEO_OUTRO_HEADLINE', 'AIVIDEO_OUTRO_SUBLINE',
    'AIVIDEO_OUTRO_NARRATION', 'AIVIDEO_OUTRO_NARRATION_VARIANTS'
)) {
    Remove-Item -Path "Env:$key" -ErrorAction SilentlyContinue
}

Load-EnvSections -FilePath (Join-Path $Root '.env') -WantLocale $Locale
$env:AIVIDEO_LOCALE = $Locale
$env:ROOT = $Root
