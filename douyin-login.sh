#!/usr/bin/env bash
# 首次登录 / 续期抖音创作者平台 cookie（需有头浏览器扫码）
# 用法:
#   ./douyin-login.sh           # 仅在需要时打开浏览器
#   ./douyin-login.sh --force   # 清除旧 cookie，强制重新扫码
#   ./douyin-login.sh --check   # 只校验上传页登录态
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
[[ -f .env ]] && source .env

SAU_HOME="${SAU_HOME:-$ROOT/vendor/social-auto-upload}"
SAU_BIN="${SAU_BIN:-$SAU_HOME/.venv/bin/sau}"
SAU_PY="${SAU_HOME}/.venv/bin/python3"
ACCOUNT="${SAU_DOUYIN_ACCOUNT:-main}"
MAC_CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
COOKIE_FILE="$SAU_HOME/cookies/douyin_${ACCOUNT}.json"
PROFILE_DIR="$SAU_HOME/cookies/browser_profiles/douyin_${ACCOUNT}"

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

python3 "$ROOT/src/apply_sau_patches.py"

if [[ -x "$MAC_CHROME" ]] && [[ -f "$SAU_HOME/conf.py" ]]; then
  python3 - "$SAU_HOME/conf.py" "$MAC_CHROME" <<'PY'
import re
import sys
from pathlib import Path

conf_path = Path(sys.argv[1])
chrome_path = sys.argv[2]
text = conf_path.read_text(encoding="utf-8")
if not re.search(r'LOCAL_CHROME_PATH\s*=\s*["\'][^"\']+["\']', text):
    text = re.sub(
        r'LOCAL_CHROME_PATH\s*=.*',
        f'LOCAL_CHROME_PATH = "{chrome_path}"',
        text,
        count=1,
    )
    conf_path.write_text(text, encoding="utf-8")
    print(f"已配置 LOCAL_CHROME_PATH={chrome_path}")
PY
fi

session_check() {
  if [[ ! -x "$SAU_PY" ]]; then
    echo "未找到 $SAU_PY，请先运行: ./setup-sau.sh" >&2
    return 1
  fi
  "$SAU_PY" "$ROOT/src/douyin_session.py" --account "$ACCOUNT"
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
    echo "无需扫码。若发布仍失败，请执行: ./douyin-login.sh --force"
    exit 0
  fi
  echo "当前登录态无效或上传页未就绪，将打开浏览器重新登录。"
  echo ""
fi

echo "登录提示（若手机显示「系统繁忙」请按此操作）："
echo "  1. 优先在弹出的 Chrome 窗口里扫码，不要扫终端/PNG 里的旧码"
echo "  2. 关闭 VPN/代理，手机和电脑在同一网络"
echo "  3. 若仍繁忙：先在 Chrome 手动打开 https://creator.douyin.com/ 登录一次，再重跑本脚本"
echo ""
echo "即将打开 Chrome，请在窗口内完成扫码…"
echo ""

cd "$SAU_HOME"
"$SAU_BIN" douyin login --account "$ACCOUNT" --headed

echo ""
echo "登录流程结束，正在验证上传页…"
if session_check; then
  echo "验证通过，可以执行 ./publish-all-douyin.sh"
  exit 0
fi

echo ""
echo "登录后上传页仍未就绪。请再试一次: ./douyin-login.sh --force" >&2
exit 1
