#!/usr/bin/env bash
# 本地合成：脚本 + 配图 + TTS + ffmpeg → output/xxx.mp4
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

: "${DASHSCOPE_API_KEY:?请设置 DASHSCOPE_API_KEY}"

SCRIPT="${1:-${AIVIDEO_SCRIPT:-$ROOT/logs/last_script.json}}"
[[ "$SCRIPT" != /* ]] && SCRIPT="$ROOT/$SCRIPT"
shift || true

echo "[compose] 脚本: $SCRIPT"
echo "[compose] 模型: ${DASHSCOPE_TTS_MODEL:-cosyvoice-v2} 音色: ${DASHSCOPE_TTS_VOICE:-longshu_v2}"
python3 "$ROOT/src/video_compose.py" "$SCRIPT" "$@"
