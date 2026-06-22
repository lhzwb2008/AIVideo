# Load env vars from .env by locale section (mirrors scripts/load-dotenv.sh)
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
    Get-Content -LiteralPath $FilePath -Encoding UTF8 | ForEach-Object {
        $line = $_
        if ($line -match '^#==\s*section:\s*(\w+)\s*==') {
            $section = $Matches[1].ToLower()
            return
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
