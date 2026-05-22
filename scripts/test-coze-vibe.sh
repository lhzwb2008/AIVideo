#!/usr/bin/env bash
# 检测扣子编程部署 API（graph_parameter + 可选试跑 run）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] || { echo "缺少 .env"; exit 1; }
# shellcheck disable=SC1091
source .env

: "${COZE_VIBE_API_TOKEN:?}"
GRAPH_URL="${COZE_VIBE_GRAPH_URL:-${COZE_VIBE_BASE_URL%/}/graph_parameter}"
RUN_URL="${COZE_VIBE_RUN_URL:-${COZE_VIBE_BASE_URL%/}/run}"

echo "=== graph_parameter ==="
echo "GET $GRAPH_URL"
curl -sS -m 30 "$GRAPH_URL" \
  -H "Authorization: Bearer ${COZE_VIBE_API_TOKEN}" \
  | python3 -m json.tool

echo ""
echo "✅ graph_parameter 可达；执行成片请: ./scripts/run-coze-vibe-workflow.sh"
