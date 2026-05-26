#!/usr/bin/env bash
# 批量：近 N 天 AI 圈热门英文长文 → 多条视频（制作与发布分离）
# 用法:
#   ./run-batch-aivideo.sh              # 默认 10 条 / 近 7 天
#   ./run-batch-aivideo.sh --count 5
#   ./publish-all-douyin.sh             # 制作完成后手动发布
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && source .env
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

exec python3 "$ROOT/src/batch_aivideo.py" "$@"
