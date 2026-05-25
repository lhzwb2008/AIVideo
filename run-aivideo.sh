#!/usr/bin/env bash
# 完整流程：Cursor 调研 → Coze 合成 →（可选）抖音发布
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && source .env

TOPIC="${1:-${COZE_WORKFLOW_TOPIC:-AI热点深度}}"
SCRIPT="${AIVIDEO_SCRIPT:-$ROOT/logs/last_script.json}"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

echo "=== AIVideo：单话题深度 + 合成 ==="
echo "检索方向: ${TOPIC}（Agent 将自选当日最热 AI 单话题）"
echo ""

python3 "$ROOT/src/research.py" "$TOPIC" -o "$SCRIPT"
echo ""
if [[ -n "${AIHUBMIX_API_KEY:-}" ]]; then
  "$ROOT/run-enrich-images.sh" "$SCRIPT"
  echo ""
else
  echo "未设置 AIHUBMIX_API_KEY，跳过 API 生图（仍由 Coze 内部生图）"
  echo ""
fi
"$ROOT/run-coze.sh" "$SCRIPT"
echo ""
echo "视频已保存到 output/。发布抖音请手动运行: ./publish-all-douyin.sh"
