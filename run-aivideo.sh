#!/usr/bin/env bash
# 完整流程：Cursor Cloud 找文章+深读 → Opus 4.7 评审+改编 → AiHubMix 生图 → 本地 TTS+ffmpeg 合成
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && source .env

SCRIPT="${AIVIDEO_SCRIPT:-$ROOT/logs/last_script.json}"
DAYS="${AIVIDEO_DAYS:-1}"
SOURCE="${AIVIDEO_SOURCE:-feeds}"
FRESH_HOURS="${AIVIDEO_FRESH_HOURS:-24}"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

echo "=== AIVideo：AI财知道 → 中文问答短视频 ==="
echo "策略:  固定优质信息源近 ${FRESH_HOURS} 小时 AI/财经热点 → AI 自动选 → 深读改编 3-10 页"
echo ""

RESEARCH_EXTRA=()
if [[ "${AIVIDEO_USE_SELECTION:-}" == "1" && -f "$ROOT/logs/last_article.json" ]]; then
  RESEARCH_EXTRA+=(--use-selection)
  echo "（复用 logs/last_article.json，跳过重新找文章）"
fi
# 默认 AI 自动评审挑文章；设 AIVIDEO_MANUAL_PICK=1 切回人工选
if [[ "${AIVIDEO_MANUAL_PICK:-}" != "1" ]]; then
  RESEARCH_EXTRA+=(--auto-pick)
fi

python3 "$ROOT/src/research.py" -o "$SCRIPT" --days "$DAYS" --source "$SOURCE" --fresh-hours "$FRESH_HOURS" \
  ${RESEARCH_EXTRA[@]+"${RESEARCH_EXTRA[@]}"}
echo ""

: "${AIHUBMIX_API_KEY:?请在 .env 设置 AIHUBMIX_API_KEY}"
"$ROOT/run-enrich-images.sh" "$SCRIPT"
echo ""

"$ROOT/run-compose.sh" "$SCRIPT"
echo ""
ROOT_FOR_AIVIDEO="$ROOT" AIVIDEO_SCRIPT_PATH="$SCRIPT" python3 - <<'PY'
import os
from pathlib import Path
from batch_aivideo import append_history_from_script

root = Path(os.environ.get("ROOT_FOR_AIVIDEO", ".")).resolve()
script = Path(os.environ.get("AIVIDEO_SCRIPT_PATH", "logs/last_script.json"))
if not script.is_absolute():
    script = root / script
last_video = root / "logs" / "last_video.txt"
video = None
if last_video.is_file():
    raw = last_video.read_text(encoding="utf-8").strip()
    video = Path(raw)
    if not video.is_absolute():
        video = root / video
append_history_from_script(script, video)
PY
echo "视频已保存到 output/。发布抖音请手动运行: ./publish-all-douyin.sh"
