#!/usr/bin/env bash
# YouTube OAuth 授权（首次 / token 失效时）
# 用法:
#   ./youtube-login.sh
#   ./youtube-login.sh --force    # 清除旧 token 重新授权
#   ./youtube-login.sh --check    # 只校验
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a
# 国内访问 Google API 需代理，可在 .env 设 YOUTUBE_HTTP_PROXY=http://127.0.0.1:7897
if [[ -n "${YOUTUBE_HTTP_PROXY:-}" ]]; then
  export http_proxy="$YOUTUBE_HTTP_PROXY" https_proxy="$YOUTUBE_HTTP_PROXY"
  [[ -n "${YOUTUBE_ALL_PROXY:-}" ]] && export all_proxy="$YOUTUBE_ALL_PROXY"
fi

PY="$ROOT/.venv/bin/python3"
if [[ ! -x "$PY" ]]; then
  echo "未找到 .venv，请先运行: ./setup-youtube.sh" >&2
  exit 1
fi

ACTION=login
EXTRA=()
for arg in "$@"; do
  case "$arg" in
    --force) EXTRA+=(--force) ;;
    --check) ACTION=check ;;
  esac
done

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" "$ROOT/src/publish_youtube.py" "$ACTION" ${EXTRA[@]+"${EXTRA[@]}"}
