#!/usr/bin/env bash
# AI财知道：Exa 找选题 → 生成视频 → 自动发布抖音 → 成功后归档
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && set -a && source .env && set +a
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

COUNT="${1:-${DAILY_RUN_COUNT:-1}}"
DAYS="${AIVIDEO_DAYS:-${DAILY_RUN_DAYS:-1}}"

python3 "$ROOT/src/make_publish.py" --count "$COUNT" --days "$DAYS" --check
