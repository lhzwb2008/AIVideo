#!/usr/bin/env bash
# 微信公众平台登录（扫码，保存 cookie 供 Playwright 发文）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

SAU_PY="${SAU_HOME:-$ROOT/vendor/social-auto-upload}/.venv/bin/python3"
if [[ ! -x "$SAU_PY" ]]; then
  SAU_PY="python3"
fi

ACCOUNT="${WECHAT_ACCOUNT:-main}"
COOKIE_FILE="${SAU_HOME:-$ROOT/vendor/social-auto-upload}/cookies/wechat_${ACCOUNT}.json"

FORCE=0
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --check) CHECK_ONLY=1 ;;
  esac
done

session_check() {
  "$SAU_PY" "$ROOT/src/wechat_session.py" --account "$ACCOUNT"
}

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  session_check
  exit $?
fi

if [[ "$FORCE" -eq 1 ]]; then
  rm -f "$COOKIE_FILE"
  rm -rf "${SAU_HOME:-$ROOT/vendor/social-auto-upload}/cookies/browser_profiles/wechat_${ACCOUNT}"
fi

if [[ "$FORCE" -eq 0 ]] && [[ -f "$COOKIE_FILE" ]]; then
  if session_check >/dev/null 2>&1; then
    session_check
    echo "无需重新登录。若发文失败: ./wechat-login.sh --force"
    exit 0
  fi
fi

echo "即将打开 Chrome，请扫码登录微信公众平台…"
"$SAU_PY" "$ROOT/src/wechat_session.py" --login --account "$ACCOUNT"
session_check
