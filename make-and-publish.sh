#!/usr/bin/env bash
# AI财知道：每日热点 → 问句话题 → 搜文深读改编 → 生成视频 → 发布
# 默认每天 3 条（A股 / AI / 港美股 各 1），与 make-topics 实验模式一致
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export ROOT
source "$ROOT/scripts/load-dotenv.sh" zh
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

COUNT="${1:-${AIVIDEO_MAX_VIDEOS_PER_RUN:-3}}"
DAYS="${AIVIDEO_DAYS:-${DAILY_RUN_DAYS:-3}}"

PY="$ROOT/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi
exec "$PY" "$ROOT/src/make_publish.py" --count "$COUNT" --days "$DAYS"
