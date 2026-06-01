#!/usr/bin/env bash
# 发布单条 MP4 到 小红书 / 视频号（复用 vendor/social-auto-upload）
# 用法:
#   scripts/publish-social.sh <platform> <video.mp4> [--script logs/xxx.json] [--dry-run] [--headed]
#   platform: xiaohongshu | shipinhao
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a

SAU_HOME="${SAU_HOME:-$ROOT/vendor/social-auto-upload}"
SAU_PY="${SAU_HOME}/.venv/bin/python3"

if [[ ! -x "$SAU_PY" ]]; then
  echo "未找到 $SAU_PY，请先运行: ./setup-sau.sh" >&2
  exit 1
fi
if [[ $# -lt 1 ]]; then
  echo "用法: scripts/publish-social.sh <xiaohongshu|shipinhao> <video.mp4> [opts]" >&2
  exit 2
fi

PLATFORM="$1"; shift
export SAU_HOME
export PYTHONPATH="$ROOT/src:$SAU_HOME${PYTHONPATH:+:$PYTHONPATH}"

exec "$SAU_PY" "$ROOT/src/social_publisher.py" "$PLATFORM" publish "$@"
