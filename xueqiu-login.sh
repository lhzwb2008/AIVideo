#!/usr/bin/env bash
# 雪球创作者中心登录（扫码/短信，保存 cookie 供 Playwright 发文）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
source "$ROOT/scripts/load-dotenv.sh" "${AIVIDEO_LOCALE:-zh}"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

SAU_PY="${SAU_HOME:-$ROOT/vendor/social-auto-upload}/.venv/bin/python3"
if [[ ! -x "$SAU_PY" ]]; then
  SAU_PY="python3"
fi

ACCOUNT="${XUEQIU_ACCOUNT:-main}"
SAU_ROOT="${SAU_HOME:-$ROOT/vendor/social-auto-upload}"
COOKIE_FILE="${SAU_ROOT}/cookies/xueqiu_${ACCOUNT}.json"
PROFILE_DIR="${SAU_ROOT}/cookies/browser_profiles/xueqiu_${ACCOUNT}"

FORCE=0
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --check) CHECK_ONLY=1 ;;
  esac
done

session_check() {
  "$SAU_PY" "$ROOT/src/xueqiu_session.py" --account "$ACCOUNT"
}

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  session_check
  exit $?
fi

if [[ "$FORCE" -eq 1 ]]; then
  rm -f "$COOKIE_FILE"
  rm -rf "$PROFILE_DIR"
fi

if [[ "$FORCE" -eq 0 ]] && [[ -f "$COOKIE_FILE" ]]; then
  if session_check >/dev/null 2>&1; then
    session_check
    echo "无需重新登录。若发文失败: ./xueqiu-login.sh --force"
    exit 0
  fi
fi

echo "即将打开 Chrome，请登录雪球创作者中心…"
"$SAU_PY" "$ROOT/src/xueqiu_session.py" --login --account "$ACCOUNT"
session_check
