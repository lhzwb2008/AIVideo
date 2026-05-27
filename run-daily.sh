#!/usr/bin/env bash
# 每日定时任务：搜近 24h 中英文 AI/财经热点 → 生成 2 个问答视频 → 发布抖音 → 归档已发布
# 由 launchd / cron 在每天早 10:00 调起
#
# 用法:
#   ./run-daily.sh                # 默认 2 条 / 近 1 天
#   ./run-daily.sh --count 3
#   ./run-daily.sh --skip-publish # 只制作不发布
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && source .env
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

exec python3 "$ROOT/src/run_daily.py" "$@"
