#!/usr/bin/env bash
# 扫码登录 / 续期 小红书 / 视频号 cookie（复用 vendor/social-auto-upload）
# 用法:
#   ./social-login.sh <platform> [--check] [--force] [--account NAME] [--headless]
#   platform: xiaohongshu | shipinhao
#
# 登录新账号（不覆盖 main）:
#   ./social-login.sh xiaohongshu --account newid
#   然后在 .env 设 SAU_XHS_ACCOUNT=newid
#
# 强制换号 / 重新扫码（删旧 cookie）:
#   ./social-login.sh xiaohongshu --force
#   ./social-login.sh xiaohongshu --account newid --force
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a

SAU_HOME="${SAU_HOME:-$ROOT/vendor/social-auto-upload}"
SAU_PY="${SAU_HOME}/.venv/bin/python3"

if [[ ! -x "$SAU_PY" ]]; then
  echo "未找到 $SAU_PY，请先运行: ./setup-sau.sh" >&2
  exit 1
fi
if [[ $# -lt 1 ]]; then
  echo "用法: ./social-login.sh <xiaohongshu|shipinhao> [--check] [--force] [--account NAME] [--headless]" >&2
  exit 2
fi

PLATFORM="$1"
shift

ACCOUNT=""
FORCE=0
CHECK_ONLY=0
HEADLESS=0
EXTRA=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) CHECK_ONLY=1 ;;
    --force) FORCE=1 ;;
    --account)
      shift
      ACCOUNT="${1:?--account 需要账号名（如 main / backup）}"
      ;;
    --headless) HEADLESS=1 ;;
    *)
      echo "未知参数: $1（支持: --check --force --account NAME --headless）" >&2
      exit 2
      ;;
  esac
  shift
done

case "$PLATFORM" in
  xiaohongshu|xhs|redbook)
    PUB_PLATFORM="xiaohongshu"
    COOKIE_KEY="xiaohongshu"
    ACCOUNT="${ACCOUNT:-${SAU_XHS_ACCOUNT:-main}}"
    export SAU_XHS_ACCOUNT="$ACCOUNT"
    ;;
  shipinhao|tencent|weixin|channels)
    PUB_PLATFORM="shipinhao"
    COOKIE_KEY="tencent"
    ACCOUNT="${ACCOUNT:-${SAU_SHIPINHAO_ACCOUNT:-main}}"
    export SAU_SHIPINHAO_ACCOUNT="$ACCOUNT"
    ;;
  *)
    echo "未知平台: $PLATFORM（可选: xiaohongshu | shipinhao）" >&2
    exit 2
    ;;
esac

COOKIE_FILE="${SAU_HOME}/cookies/${COOKIE_KEY}_${ACCOUNT}.json"
PROFILE_DIR="${SAU_HOME}/cookies/browser_profiles/${COOKIE_KEY}_${ACCOUNT}"

export SAU_HOME
export PYTHONPATH="$ROOT/src:$SAU_HOME${PYTHONPATH:+:$PYTHONPATH}"

session_check() {
  "$SAU_PY" "$ROOT/src/social_publisher.py" "$PUB_PLATFORM" check
}

echo "平台: $PUB_PLATFORM  账号: $ACCOUNT"
echo "Cookie: $COOKIE_FILE"

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  session_check
  exit $?
fi

if [[ "$FORCE" -eq 1 ]]; then
  echo "已删除旧登录态（--force）"
  rm -f "$COOKIE_FILE"
  rm -rf "$PROFILE_DIR"
fi

if [[ "$FORCE" -eq 0 ]] && [[ -f "$COOKIE_FILE" ]]; then
  if session_check >/dev/null 2>&1; then
    session_check
    echo ""
    echo "当前账号 cookie 仍有效，未打开浏览器。"
    echo "  换号登录: ./social-login.sh $PUB_PLATFORM --account <新账号名> --force"
    echo "  同账号重登: ./social-login.sh $PUB_PLATFORM --force"
    echo "  换号后请在 .env 设置 SAU_XHS_ACCOUNT=<新账号名>（视频号用 SAU_SHIPINHAO_ACCOUNT）"
    exit 0
  fi
fi

echo "即将打开浏览器，请扫码完成登录…"
LOGIN_ARGS=(login)
[[ "$HEADLESS" -eq 1 ]] && LOGIN_ARGS+=(--headless)
exec "$SAU_PY" "$ROOT/src/social_publisher.py" "$PUB_PLATFORM" "${LOGIN_ARGS[@]}"
