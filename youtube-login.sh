#!/usr/bin/env bash
# YouTube OAuth 授权（首次 / token 失效时）
# 用法:
#   ./youtube-login.sh
#   ./youtube-login.sh --force    # 清除旧 token 重新授权
#   ./youtube-login.sh --check    # 只校验
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
source "$ROOT/scripts/load-dotenv.sh" "${AIVIDEO_LOCALE:-en}"

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

if [[ "$ACTION" == "login" && ${#EXTRA[@]} -eq 0 ]]; then
  TOKEN="$ROOT/credentials/youtube/${YOUTUBE_ACCOUNT:-main}_token.json"
  if [[ -f "$TOKEN" ]]; then
    echo "提示: token 若已过期，请用 ./youtube-login.sh --force 重新授权" >&2
  fi
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PY" "$ROOT/src/publish_youtube.py" "$ACTION" ${EXTRA[@]+"${EXTRA[@]}"}
