#!/usr/bin/env bash
# 生成英文男声试听样本 → output/voice_previews/
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/load-dotenv.sh" "${AIVIDEO_LOCALE:-en}"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
PY="$ROOT/.venv/bin/python3"
[[ -x "$PY" ]] || PY="python3"
exec "$PY" "$ROOT/src/preview_us_voices.py" "$@"
