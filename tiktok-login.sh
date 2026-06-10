#!/usr/bin/env bash
# TikTok OAuth 授权（Desktop Login Kit + PKCE）
#
#   ./tiktok-login.sh
#   ./tiktok-login.sh --force
#   ./tiktok-login.sh --check
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
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
ACTION="login"
EXTRA=()
for arg in "$@"; do
  case "$arg" in
    --check) ACTION="check" ;;
    --force) EXTRA+=("--force") ;;
    *) EXTRA+=("$arg") ;;
  esac
done
exec "$PY" "$ROOT/src/publish_tiktok.py" "$ACTION" ${EXTRA[@]+"${EXTRA[@]}"}
