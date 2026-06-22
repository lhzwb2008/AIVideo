#!/usr/bin/env bash
# 测试 Instagram / Facebook Reels / LinkedIn 浏览器发布（不入主流程）
#
#   ./scripts/test-us-social.sh login instagram --manual
#   ./scripts/test-us-social.sh login instagram
#   ./scripts/test-us-social.sh check all
#   ./scripts/test-us-social.sh publish instagram --assist
#   ./scripts/test-us-social.sh publish all --video output/en/xxx.mp4
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export ROOT
source "$ROOT/scripts/load-dotenv.sh" en 2>/dev/null || true
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

export AIVIDEO_US_CREDENTIALS_DIR="${AIVIDEO_US_CREDENTIALS_DIR:-$ROOT/credentials/us}"
export YOUTUBE_CREDENTIALS_DIR="${YOUTUBE_CREDENTIALS_DIR:-$AIVIDEO_US_CREDENTIALS_DIR/youtube}"
export TIKTOK_CREDENTIALS_DIR="${TIKTOK_CREDENTIALS_DIR:-$AIVIDEO_US_CREDENTIALS_DIR/tiktok}"
export AIVIDEO_US_SOCIAL_DIR="${AIVIDEO_US_SOCIAL_DIR:-$AIVIDEO_US_CREDENTIALS_DIR/social}"

PY="$ROOT/vendor/social-auto-upload/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY="$ROOT/.venv/bin/python3"
fi
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

exec "$PY" "$ROOT/scripts/test_us_social_publish.py" "$@"
