#!/usr/bin/env bash
# B 站创作中心登录 / 续期（Chrome 扫码，与抖音/小红书相同方式）
# 用法:
#   ./bilibili-login.sh           # 仅在需要时打开浏览器
#   ./bilibili-login.sh --force   # 清除旧 cookie 与 profile 后重新扫码
#   ./bilibili-login.sh --check   # 只校验登录态
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
source "$ROOT/scripts/load-dotenv.sh" "${AIVIDEO_LOCALE:-zh}"

SAU_HOME="${SAU_HOME:-$ROOT/vendor/social-auto-upload}"
SAU_PY="${SAU_HOME}/.venv/bin/python3"
if [[ ! -x "$SAU_PY" ]]; then
  SAU_PY="${SAU_HOME}/.venv/Scripts/python.exe"
fi
ACCOUNT="${SAU_BILIBILI_ACCOUNT:-main}"
COOKIE_FILE="$SAU_HOME/cookies/bilibili_${ACCOUNT}.json"
PROFILE_DIR="$SAU_HOME/cookies/browser_profiles/bilibili_${ACCOUNT}"

FORCE=0
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --check) CHECK_ONLY=1 ;;
  esac
done

if [[ ! -x "$SAU_PY" ]]; then
  echo "未找到 SAU venv，请先运行: ./setup-sau.sh" >&2
  exit 1
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
python3 "$ROOT/src/apply_sau_patches.py"

session_check() {
  "$SAU_PY" "$ROOT/src/bilibili_session.py" --account "$ACCOUNT"
}

echo "账号: $ACCOUNT"
echo ""

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  session_check
  exit $?
fi

if [[ "$FORCE" -eq 1 ]]; then
  echo "强制重新登录：清除旧 cookie 与浏览器 profile…"
  rm -f "$COOKIE_FILE"
  rm -rf "$PROFILE_DIR"
  echo ""
fi

if [[ "$FORCE" -eq 0 ]]; then
  if session_check >/dev/null 2>&1; then
    session_check
    echo ""
    echo "无需扫码。若发布仍失败，请执行: ./bilibili-login.sh --force"
    exit 0
  fi
  echo "当前登录态无效或上传页未就绪，将打开浏览器重新登录。"
  echo ""
fi

echo "即将打开 Chrome，请在窗口内用 B 站 App 扫码登录。"
echo "创作中心: https://member.bilibili.com/platform/home"
echo ""

"$SAU_PY" "$ROOT/src/bilibili_login.py" --login --account "$ACCOUNT"

echo ""
echo "登录流程结束，正在验证上传页…"
if session_check; then
  echo "验证通过。可在 .env 设 AIVIDEO_PUBLISH_BILIBILI=1 后运行 ./make-and-publish.sh"
  exit 0
fi

echo "登录后上传页仍未就绪。请再试一次: ./bilibili-login.sh --force" >&2
exit 1
