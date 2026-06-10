#!/usr/bin/env bash
# 发布单条 MP4 到 YouTube Shorts
# 用法:
#   scripts/publish-youtube.sh output/xxx.mp4 --script logs/xxx.json
#   scripts/publish-youtube.sh --dry-run
#   .venv/bin/python3 src/publish_youtube.py set-privacy last   # 把上次上传改为 public
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/load-dotenv.sh" "${AIVIDEO_LOCALE:-en}"
# 代理仅由 Python 读取 YOUTUBE_HTTP_PROXY，不 export 到环境，避免误伤其它请求
if [[ -n "${YOUTUBE_HTTP_PROXY:-}" ]]; then
  echo "YouTube: 使用 .env 中的 YOUTUBE_HTTP_PROXY"
fi

PY="$ROOT/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  echo "未找到 .venv，请先运行: ./setup-youtube.sh" >&2
  exit 1
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" "$ROOT/src/publish_youtube.py" publish "$@"
