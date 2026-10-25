# 把 .env 精简成只含密钥 / 账号标识 / 本机路径，其余交给 config/defaults.env。
# 用法：
#   .\scripts\slim-env.ps1          只对比：列出与默认值不同、或默认值里没有的项（不改文件）
#   .\scripts\slim-env.ps1 -Apply   备份为 .env.full.local 后改写 .env
param([switch]$Apply)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$EnvFile = Join-Path $Root '.env'
$DefaultsFile = Join-Path $Root 'config\defaults.env'
$utf8 = New-Object System.Text.UTF8Encoding $false

$KeepPattern = '(KEY|SECRET|TOKEN|PASSWORD|COOKIE)|^(OSS_BUCKET|OSS_PREFIX|OSS_REGION|OSS_ENDPOINT|WECHAT_APP_ID|CURSOR_SANDBOX_REPO_URL|CURSOR_SANDBOX_REPO_REF|SAU_BIN|SAU_HOME|FFMPEG_PATH|LOCAL_CHROME_PATH)$'

function Read-Effective([string]$Path) {
    $vals = [ordered]@{}
    $section = 'shared'
    foreach ($line in [System.IO.File]::ReadAllLines($Path, $utf8)) {
        if ($line -match '^#==\s*section:\s*(\w+)\s*==') { $section = $Matches[1].ToLower(); continue }
        if ($line -match '^\s*#' -or $line -notmatch '=') { continue }
        if ($section -ne 'shared' -and $section -ne 'zh') { continue }
        $key = ($line -split '=', 2)[0].Trim()
        $val = ($line -split '=', 2)[1].Trim()
        if ($val -match '^"(.*)"$') { $val = $Matches[1] } elseif ($val -match "^'(.*)'$") { $val = $Matches[1] }
        if (-not $key) { continue }
        if ($section -eq 'zh' -or -not $vals.Contains($key)) { $vals[$key] = $val }
    }
    return $vals
}

if (-not (Test-Path $EnvFile)) { throw ".env 不存在：$EnvFile" }
$cur = Read-Effective $EnvFile
$def = Read-Effective $DefaultsFile

$keep = [ordered]@{}
$changed = @()
$dropped = @()
foreach ($k in $cur.Keys) {
    $v = $cur[$k]
    if ($k -match $KeepPattern) {
        if ($v) { $keep[$k] = $v }
    } elseif (-not $def.Contains($k)) {
        if ($v) { $dropped += "  $k=$v" }
    } elseif ($def[$k] -ne $v) {
        $changed += "  $k：现在 $v → 默认 $($def[$k])"
    }
}

Write-Host "保留在 .env 的项（不显示值）："
$keep.Keys | ForEach-Object { Write-Host "  $_" }
Write-Host ""
if ($changed) { Write-Host "与默认值不同（精简后改用默认值，想保留就加回 .env）："; $changed | ForEach-Object { Write-Host $_ }; Write-Host "" }
if ($dropped) { Write-Host "默认值里没有（精简后会去掉，想保留就加回 .env）："; $dropped | ForEach-Object { Write-Host $_ }; Write-Host "" }
if (-not $changed -and -not $dropped) { Write-Host "其余配置与默认值完全一致。"; Write-Host "" }

if (-not $Apply) {
    Write-Host "只是对比，未改文件。确认后执行：.\scripts\slim-env.ps1 -Apply"
    return
}

Copy-Item $EnvFile (Join-Path $Root '.env.full.local') -Force
$lines = @('# 只放密钥 / 账号标识 / 本机路径；其余默认值见 config/defaults.env（同名写在这里可覆盖）')
foreach ($k in $keep.Keys) { $lines += "$k=$($keep[$k])" }
[System.IO.File]::WriteAllLines($EnvFile, [string[]]$lines, $utf8)
Write-Host "已改写 .env（$($keep.Count) 项），原文件备份在 .env.full.local"
