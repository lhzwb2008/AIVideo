#!/usr/bin/env bash
# AI财知道：每日热点 → 问句话题 → 搜文深读改编 → 生成视频 → 抖音 → 归档 → YouTube / 小红书等
# 默认每天 3 条（A股 / AI / 港美股 各 1），与 make-topics 实验模式一致
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && set -a && source .env && set +a
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

COUNT="${1:-${AIVIDEO_MAX_VIDEOS_PER_RUN:-3}}"
# 发现热点候选的时间窗；单条话题搜文用 AIVIDEO_TOPIC_DAYS（默认 7）
DAYS="${AIVIDEO_DAYS:-${DAILY_RUN_DAYS:-7}}"

python3 "$ROOT/src/make_publish.py" --count "$COUNT" --days "$DAYS" --check
