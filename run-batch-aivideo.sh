#!/usr/bin/env bash
# 批量：近 1 个月 AI 新闻 → N 条视频（制作与发布分离）
# 用法:
#   ./run-batch-aivideo.sh              # 默认 10 条 / 近 30 天
#   ./run-batch-aivideo.sh --count 5
#   ./publish-all-douyin.sh             # 制作完成后手动发布
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && source .env
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

exec python3 "$ROOT/src/batch_aivideo.py" "$@"
