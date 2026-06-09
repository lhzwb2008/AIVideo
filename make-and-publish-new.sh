#!/usr/bin/env bash
# AI财知道（Cursor 调研版）：
#   固定五槽位 → Cursor Cloud Agent 联网写稿 → Opus 深读改编 → 生图合成发布
#   不再用 Exa + Opus 选题；顺序：A股大盘 → A股板块 → 国内财经 → AI → 世界财经
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
export ROOT
source "$ROOT/scripts/load-dotenv.sh" zh
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
export AIVIDEO_SOURCE=cursor
export AIVIDEO_COMPLIANCE_RELAXED=1

COUNT="${1:-${AIVIDEO_MAX_VIDEOS_PER_RUN:-5}}"

PY="$ROOT/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  PY="python3"
fi
exec "$PY" "$ROOT/src/make_publish_new.py" --count "$COUNT" "${@:2}"
