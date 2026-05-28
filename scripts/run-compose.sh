#!/usr/bin/env bash
# 本地合成：脚本 + 配图 + TTS + ffmpeg → output/xxx.mp4
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

TTS_PROVIDER="${TTS_PROVIDER:-doubao}"
if [[ "$TTS_PROVIDER" == "doubao" || "$TTS_PROVIDER" == "volcengine" || "$TTS_PROVIDER" == "volc" ]]; then
  : "${VOLCENGINE_TTS_API_KEY:?请设置 VOLCENGINE_TTS_API_KEY}"
else
  : "${DASHSCOPE_API_KEY:?请设置 DASHSCOPE_API_KEY}"
fi

SCRIPT="${1:-${AIVIDEO_SCRIPT:-$ROOT/logs/last_script.json}}"
[[ "$SCRIPT" != /* ]] && SCRIPT="$ROOT/$SCRIPT"
shift || true

echo "[compose] 脚本: $SCRIPT"
if [[ "$TTS_PROVIDER" == "doubao" || "$TTS_PROVIDER" == "volcengine" || "$TTS_PROVIDER" == "volc" ]]; then
  echo "[compose] TTS: doubao ${VOLCENGINE_TTS_RESOURCE_ID:-seed-icl-2.0} 音色: ${VOLCENGINE_TTS_SPEAKER:-S_6uN8A8f22}"
else
  echo "[compose] TTS: dashscope ${DASHSCOPE_TTS_MODEL:-cosyvoice-v2} 音色: ${DASHSCOPE_TTS_VOICE:-longshu_v2}"
fi
python3 "$ROOT/src/video_compose.py" "$SCRIPT" "$@"
