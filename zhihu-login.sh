#!/usr/bin/env bash
# 知乎专栏登录（扫码/短信，保存 cookie 供 Playwright 保存草稿）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

SAU_PY="${SAU_HOME:-$ROOT/vendor/social-auto-upload}/.venv/bin/python3"
if [[ ! -x "$SAU_PY" ]]; then
  SAU_PY="python3"
fi

ACCOUNT="${ZHIHU_ACCOUNT:-main}"
COOKIE_FILE="${SAU_HOME:-$ROOT/vendor/social-auto-upload}/cookies/zhihu_${ACCOUNT}.json"

CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=1 ;;
  esac
done

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  exec "$SAU_PY" "$ROOT/src/zhihu_session.py" --account "$ACCOUNT"
fi

echo "知乎专栏登录 · 账号: $ACCOUNT"
echo "Cookie: $COOKIE_FILE"
exec "$SAU_PY" "$ROOT/src/zhihu_session.py" --account "$ACCOUNT" --login
