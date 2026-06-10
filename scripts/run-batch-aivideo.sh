#!/usr/bin/env bash
# 批量：近 N 天 AI/财经热门长文 → 多条问答视频（制作与发布分离）
# 用法:
#   ./scripts/run-batch-aivideo.sh              # 默认 10 条 / 近 7 天
#   ./scripts/run-batch-aivideo.sh --count 5
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

source "$ROOT/scripts/load-dotenv.sh" "${AIVIDEO_LOCALE:-zh}"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

exec python3 "$ROOT/src/batch_aivideo.py" "$@"
