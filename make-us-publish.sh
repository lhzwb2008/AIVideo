#!/usr/bin/env bash
# US Market：Cursor 联网写稿（不走 Exa）→ 英文漫画口播短视频 → YouTube/TikTok/IG/FB/LinkedIn
#
#   ./make-us-publish.sh              # 默认一轮 4 条（四槽位各 1）
#   ./make-us-publish.sh 1            # 只跑 1 条
#   US_TTS_VOICE=yunzhou ./make-us-publish.sh
#   ./make-us-publish.sh --topic "Why did the Fed pause rate cuts?"
#   ./make-us-publish.sh --no-publish
#   ./make-us-publish.sh --list-voices
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export ROOT
source "$ROOT/scripts/load-dotenv.sh" en
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

COUNT="${1:-${AIVIDEO_MAX_VIDEOS_PER_RUN:-4}}"
if [[ "$COUNT" =~ ^[0-9]+$ ]]; then
  shift || true
  EXTRA=(--count "$COUNT")
else
  EXTRA=()
fi

PY="$ROOT/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi
exec "$PY" "$ROOT/src/make_us_publish.py" "${EXTRA[@]}" "$@"
