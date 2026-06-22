#Requires -Version 5.1
<#
.SYNOPSIS
  AI财知道 · Windows 中文流水线主入口（日常自动发布优先用本脚本）

.DESCRIPTION
  工作日：五槽位新闻（默认 5 条）
  周末：Opus 动态科普选题（默认 3 条，读历史去重）

.EXAMPLE
  .\make-and-publish.ps1
  .\make-and-publish.ps1 1
  .\make-and-publish.ps1 --no-publish
  .\make-and-publish.ps1 --slot edu_quant
#>
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
Set-Location $Root
$env:ROOT = $Root

. (Join-Path $Root 'scripts\load-dotenv.ps1') -Locale zh

$env:PYTHONPATH = if ($env:PYTHONPATH) { "$Root\src;$env:PYTHONPATH" } else { "$Root\src" }
$env:AIVIDEO_SOURCE = 'cursor'
$env:AIVIDEO_COMPLIANCE_RELAXED = '1'

$Py = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    $Py = 'python'
}

$ModeInfo = & $Py -c @'
from weekend_edu_topics import is_weekend_edu_mode, weekend_default_count
from cursor_daily_topics import CURSOR_SLOT_ORDER
if is_weekend_edu_mode():
    print(f"weekend|{weekend_default_count()}|周末科普（Opus 选题，默认 {weekend_default_count()} 条）")
else:
    print(f"weekday|{len(CURSOR_SLOT_ORDER)}|工作日新闻五槽位（默认 {len(CURSOR_SLOT_ORDER)} 条）")
'@
# python 可能有多行输出（警告等），只取含 | 的最后一行
$ModeLine = @($ModeInfo | Where-Object { $_ -and $_ -match '\|' } | Select-Object -Last 1)
if (-not $ModeLine) { throw "无法解析模式信息: $ModeInfo" }
$ModeParts = ($ModeLine -split '\|', 3)
$DefaultCount = $ModeParts[1]
Write-Host "[make-and-publish] $($ModeParts[2])" -ForegroundColor Cyan

if ($RemainingArgs.Count -gt 0 -and $RemainingArgs[0] -match '^--') {
    $Count = $DefaultCount
    $Extra = @($RemainingArgs)
} elseif ($RemainingArgs.Count -gt 0) {
    $Count = [string]$RemainingArgs[0]
    $Extra = @()
    if ($RemainingArgs.Count -gt 1) {
        $Extra = @($RemainingArgs | Select-Object -Skip 1)
    }
} else {
    $Count = if ($env:AIVIDEO_MAX_VIDEOS_PER_RUN) { [string]$env:AIVIDEO_MAX_VIDEOS_PER_RUN } else { [string]$DefaultCount }
    $Extra = @()
}

$ScriptPath = Join-Path $Root 'src\make_publish.py'
$ArgList = @($ScriptPath, '--count', $Count)
foreach ($a in $Extra) {
    if ($null -ne $a -and "$a".Length -gt 0) {
        $ArgList += [string]$a
    }
}
Write-Host "[make-and-publish] $($ArgList -join ' ')" -ForegroundColor DarkGray

# PS 5.1 的 `& python @args` 数组展开会误报「索引超出数组界限」，
# 且 $ErrorActionPreference=Stop 时可能提前退出，子进程 python 仍继续跑（日志假失败）。
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try {
    if ($Extra.Count -gt 0) {
        & $Py $ScriptPath '--count' $Count @Extra
    } else {
        & $Py $ScriptPath '--count' $Count
    }
    $ExitCode = if ($null -ne $LASTEXITCODE) { [int]$LASTEXITCODE } else { 0 }
} finally {
    $ErrorActionPreference = $prevEap
}
exit $ExitCode
