#!/usr/bin/env bash
# AI财知道：工作日五槽位新闻 / 周末三槽位科普 → Cursor 写稿 → Opus 改编 → 生图合成发布
# 工作日：A股大盘 → A股板块 → 国内财经 → AI → 世界财经（默认 5 条）
# 周末：财经基础 → 量化入门 → 估值计算（默认 3 条，科普去重）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export ROOT
source "$ROOT/scripts/load-dotenv.sh" zh
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export AIVIDEO_SOURCE=cursor
export AIVIDEO_COMPLIANCE_RELAXED=1

PY="$ROOT/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi

DEFAULT_COUNT="$("$PY" "$ROOT/src/print_publish_mode.py" | awk -F'|' '/\|/{print $2}' | tail -n1)"
COUNT="${1:-${AIVIDEO_MAX_VIDEOS_PER_RUN:-$DEFAULT_COUNT}}"

exec "$PY" "$ROOT/src/make_publish.py" --count "$COUNT" "${@:2}"
