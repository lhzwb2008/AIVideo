#!/usr/bin/env bash
# 完整流程：Cursor 调研 → Coze 合成 →（可选）抖音发布
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && source .env

TOPIC="${1:-${COZE_WORKFLOW_TOPIC:-AI热点深度}}"
SCRIPT="${AIVIDEO_SCRIPT:-$ROOT/logs/last_script.json}"

echo "=== AIVideo：单话题深度 + 合成 ==="
echo "检索方向: ${TOPIC}（Agent 将自选当日最热 AI 单话题）"
echo ""

python3 "$ROOT/lib/research.py" "$TOPIC" -o "$SCRIPT"
echo ""
"$ROOT/run-coze.sh" "$SCRIPT"

if [[ "${DOUYIN_AUTO_PUBLISH:-}" == "1" ]]; then
  echo ""
  echo "=== 发布抖音 ==="
  VIDEO=""
  [[ -f logs/last_video.txt ]] && VIDEO="$(tr -d '\n' < logs/last_video.txt)"
  python3 "$ROOT/publish-douyin.py" ${VIDEO:+"$VIDEO"} --script "$SCRIPT"
fi
