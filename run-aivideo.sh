#!/usr/bin/env bash
# 完整流程：Cursor Cloud 找文章+深读 → Opus 4.7 评审+改编 → AiHubMix 生图 → 本地 TTS+ffmpeg 合成
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && source .env

SCRIPT="${AIVIDEO_SCRIPT:-$ROOT/logs/last_script.json}"
DAYS="${AIVIDEO_DAYS:-7}"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

echo "=== AIVideo：英文长文改编 → 中文短视频 ==="
echo "策略:  搜索过去 ${DAYS} 天 AI 圈热度最高 3 篇英文长文 → AI 自动选 → 深读改编 3-10 页"
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

python3 "$ROOT/src/research.py" -o "$SCRIPT" --days "$DAYS" \
  ${RESEARCH_EXTRA[@]+"${RESEARCH_EXTRA[@]}"}
echo ""

: "${AIHUBMIX_API_KEY:?请在 .env 设置 AIHUBMIX_API_KEY}"
"$ROOT/run-enrich-images.sh" "$SCRIPT"
echo ""

"$ROOT/run-compose.sh" "$SCRIPT"
echo ""
echo "视频已保存到 output/。发布抖音请手动运行: ./publish-all-douyin.sh"
