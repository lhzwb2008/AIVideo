#!/usr/bin/env bash
# 发布单条 MP4 到 YouTube Shorts
# 用法:
#   scripts/publish-youtube.sh output/xxx.mp4 --script logs/xxx.json
#   scripts/publish-youtube.sh --dry-run
#   .venv/bin/python3 src/publish_youtube.py set-privacy last   # 把上次上传改为 public
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a
if [[ -n "${YOUTUBE_HTTP_PROXY:-}" ]]; then
  export http_proxy="$YOUTUBE_HTTP_PROXY" https_proxy="$YOUTUBE_HTTP_PROXY"
  [[ -n "${YOUTUBE_ALL_PROXY:-}" ]] && export all_proxy="$YOUTUBE_ALL_PROXY"
fi

PY="$ROOT/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  echo "未找到 .venv，请先运行: ./setup-youtube.sh" >&2
  exit 1
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" "$ROOT/src/publish_youtube.py" publish "$@"
