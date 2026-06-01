#!/usr/bin/env bash
# 把抖音已发布的存量视频批量补发到 小红书/视频号
# 用法:
#   scripts/backfill-social.sh [xiaohongshu|shipinhao] [--dry-run] [--headed] [--limit N] [--sleep S]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$ROOT/src/backfill_social.py" "$@"
