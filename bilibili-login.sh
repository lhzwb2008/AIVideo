#!/usr/bin/env bash
# B 站账号登录 / 续期（biliup 扫码，需在本机交互终端运行）
# 用法:
#   ./bilibili-login.sh           # 仅在需要时登录
#   ./bilibili-login.sh --force   # 删除旧账号文件后重新扫码
#   ./bilibili-login.sh --check   # 只校验登录态
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
source "$ROOT/scripts/load-dotenv.sh" "${AIVIDEO_LOCALE:-zh}"

SAU_HOME="${SAU_HOME:-$ROOT/vendor/social-auto-upload}"
SAU_BIN="${SAU_BIN:-$SAU_HOME/.venv/bin/sau}"
ACCOUNT="${SAU_BILIBILI_ACCOUNT:-main}"
COOKIE_FILE="$SAU_HOME/cookies/bilibili_${ACCOUNT}.json"

FORCE=0
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --check) CHECK_ONLY=1 ;;
  esac
done

if [[ ! -x "$SAU_BIN" ]]; then
  echo "未找到 sau，请先运行: ./setup-sau.sh" >&2
  exit 1
fi

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

session_check() {
  cd "$SAU_HOME"
  out="$("$SAU_BIN" bilibili check --account "$ACCOUNT" 2>&1)" || true
  echo "$out"
  echo "$out" | grep -qi '^valid$'
}

echo "账号: $ACCOUNT"
echo "账号文件: $COOKIE_FILE"
echo ""

if [[ "$CHECK_ONLY" -eq 1 ]]; then
  if session_check; then
    echo "B 站登录态有效"
    exit 0
  fi
  echo "B 站登录态无效，请运行: ./bilibili-login.sh" >&2
  exit 1
fi

if [[ "$FORCE" -eq 1 ]]; then
  echo "强制重新登录：删除旧账号文件…"
  rm -f "$COOKIE_FILE"
  echo ""
fi

if [[ "$FORCE" -eq 0 && -f "$COOKIE_FILE" ]]; then
  if session_check >/dev/null 2>&1; then
    session_check
    echo ""
    echo "无需重新登录。若上传仍失败，请执行: ./bilibili-login.sh --force"
    exit 0
  fi
  echo "当前登录态无效，将重新登录。"
  echo ""
fi

echo "即将通过 biliup 扫码登录 B 站创作中心。"
echo "若终端二维码显示不完整，可在当前目录打开 qrcode.png 扫码。"
echo "创作中心: https://member.bilibili.com/platform/home"
echo ""

cd "$SAU_HOME"
"$SAU_BIN" bilibili login --account "$ACCOUNT"

echo ""
echo "登录流程结束，正在校验…"
if session_check; then
  echo "验证通过。可在 .env 设 AIVIDEO_PUBLISH_BILIBILI=1 后运行 ./make-and-publish.sh"
  exit 0
fi

echo "登录后校验仍未通过，请重试: ./bilibili-login.sh --force" >&2
exit 1
