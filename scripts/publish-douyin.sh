#!/usr/bin/env bash
# 发布单条 MP4 到抖音
# 用法: scripts/publish-douyin.sh [output/xxx.mp4] [--assist] [--dry-run]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$ROOT/src/publish_douyin.py" "$@"
