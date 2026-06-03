#!/usr/bin/env bash
# 发布单条视频到 B 站（biliup，需先 ./bilibili-login.sh）
# 用法: scripts/publish-bilibili.sh [output/xxx.mp4] [--script logs/xxx.json] [--dry-run] [--check]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$ROOT/src/publish_bilibili.py" "$@"
