#!/usr/bin/env bash
# 为脚本 JSON 逐页调用 AiHubMix 生图，写入 image_path
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && source .env

SCRIPT="${1:-${AIVIDEO_SCRIPT:-$ROOT/logs/last_script.json}}"
if [[ "$SCRIPT" != /* ]]; then
  SCRIPT="$ROOT/$SCRIPT"
fi
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

: "${AIHUBMIX_API_KEY:?请设置 AIHUBMIX_API_KEY}"

echo "[enrich] 脚本: $SCRIPT"
echo "[enrich] 模型: ${AIHUBMIX_IMAGE_MODEL:-gpt-image-2}"
python3 "$ROOT/src/enrich_images.py" "$SCRIPT" "${@:2}"
