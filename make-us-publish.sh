#!/usr/bin/env bash
# US Market：英文美股/金融市场热点 → 漫画口播风短视频 → 仅发布 YouTube + TikTok
#
#   ./make-us-publish.sh
#   US_TTS_VOICE=longfei ./make-us-publish.sh
#   ./make-us-publish.sh --topic "Why did the Fed pause rate cuts?"
#   ./make-us-publish.sh --no-publish
#   ./make-us-publish.sh --list-voices
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && set -a && source .env && set +a
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

PY="$ROOT/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi
exec "$PY" "$ROOT/src/make_us_publish.py" "$@"
