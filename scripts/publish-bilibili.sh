#!/usr/bin/env bash
# 发布单条视频到 B 站（biliup，需先 ./bilibili-login.sh）
# 用法: scripts/publish-bilibili.sh [output/xxx.mp4] [--script logs/xxx.json] [--dry-run] [--check]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/load-dotenv.sh" "${AIVIDEO_LOCALE:-zh}"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

SAU_PY="${SAU_HOME:-$ROOT/vendor/social-auto-upload}/.venv/bin/python3"
if [[ ! -x "$SAU_PY" ]]; then
  SAU_PY="python3"
fi

exec "$SAU_PY" "$ROOT/src/publish_bilibili.py" "$@"
