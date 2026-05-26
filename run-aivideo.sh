#!/usr/bin/env bash
# 完整流程：Cursor 调研 → AiHubMix 生图 → 本地 TTS+ffmpeg 合成 →（手动）抖音发布
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && source .env

TOPIC="${1:-${AIVIDEO_TOPIC:-AI热点深度}}"
SCRIPT="${AIVIDEO_SCRIPT:-$ROOT/logs/last_script.json}"
MODE="${AIVIDEO_MODE:-topic}"   # topic = 旧的关键词模板派；article = 新的英文长文改编派
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

echo "=== AIVideo：单话题深度 + 本地合成 ==="
echo "模式:    ${MODE}"
echo "频道:    ${AIVIDEO_CHANNEL:-AI 热点解读}"
if [[ "$MODE" == "article" ]]; then
  echo "策略:    搜索 AI 圈热点英文长文，按原文叙事改编 3-10 页（无固定模板）"
else
  echo "检索方向: ${TOPIC}（Agent 先出 5 候选，你来选 1 条）"
fi
echo ""

RESEARCH_EXTRA=()
if [[ "${AIVIDEO_AUTO_PICK:-}" == "1" ]]; then
  RESEARCH_EXTRA+=(--auto-pick)
  echo "（AIVIDEO_AUTO_PICK=1：候选自动选第 1 条）"
fi

if [[ "$MODE" == "article" ]]; then
  if [[ "${AIVIDEO_USE_SELECTION:-}" == "1" && -f "$ROOT/logs/last_article.json" ]]; then
    RESEARCH_EXTRA+=(--use-selection)
    echo "（复用 logs/last_article.json，跳过重新找文章）"
  fi
  DAYS="${AIVIDEO_DAYS:-7}"
  python3 "$ROOT/src/research_article.py" -o "$SCRIPT" --days "$DAYS" \
    --channel "${AIVIDEO_CHANNEL:-AI 深度}" \
    ${RESEARCH_EXTRA[@]+"${RESEARCH_EXTRA[@]}"}
else
  if [[ "${AIVIDEO_USE_SELECTION:-}" == "1" && -f "$ROOT/logs/last_selection.json" ]]; then
    RESEARCH_EXTRA+=(--use-selection)
    echo "（使用已保存选题 logs/last_selection.json，跳过重新选题）"
  fi
  python3 "$ROOT/src/research.py" "$TOPIC" -o "$SCRIPT" ${RESEARCH_EXTRA[@]+"${RESEARCH_EXTRA[@]}"}
fi
echo ""

: "${AIHUBMIX_API_KEY:?请在 .env 设置 AIHUBMIX_API_KEY}"
"$ROOT/run-enrich-images.sh" "$SCRIPT"
echo ""

"$ROOT/run-compose.sh" "$SCRIPT"
echo ""
echo "视频已保存到 output/。发布抖音请手动运行: ./publish-all-douyin.sh"
