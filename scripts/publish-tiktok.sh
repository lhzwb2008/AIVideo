#!/usr/bin/env bash
# 发布单条 MP4 到 TikTok（Content Posting API Direct Post）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/load-dotenv.sh" "${AIVIDEO_LOCALE:-en}"
if [[ -n "${TIKTOK_HTTP_PROXY:-}" ]]; then
  export http_proxy="$TIKTOK_HTTP_PROXY" https_proxy="$TIKTOK_HTTP_PROXY"
fi

PY="$ROOT/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  echo "未找到 .venv，请先运行: ./setup-tiktok.sh" >&2
  exit 1
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" "$ROOT/src/publish_tiktok.py" publish "$@"
