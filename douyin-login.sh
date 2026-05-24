#!/usr/bin/env bash
# 首次登录 / 续期抖音创作者平台 cookie（需有头浏览器扫码）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
[[ -f .env ]] && source .env

SAU_HOME="${SAU_HOME:-$ROOT/vendor/social-auto-upload}"
SAU_BIN="${SAU_BIN:-$SAU_HOME/.venv/bin/sau}"
ACCOUNT="${SAU_DOUYIN_ACCOUNT:-main}"
MAC_CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

if [[ ! -x "$SAU_BIN" ]]; then
  echo "未找到 sau，请先运行: ./setup-sau.sh" >&2
  exit 1
fi

python3 "$ROOT/lib/apply_sau_patches.py"

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

echo "账号: $ACCOUNT"
echo ""
echo "登录提示（若手机显示「系统繁忙」请按此操作）："
echo "  1. 优先在弹出的 Chrome 窗口里扫码，不要扫终端/PNG 里的旧码"
echo "  2. 关闭 VPN/代理，手机和电脑在同一网络"
echo "  3. 若仍繁忙：先在 Chrome 手动打开 https://creator.douyin.com/ 登录一次，再重跑本脚本"
echo ""
cd "$SAU_HOME"
exec "$SAU_BIN" douyin login --account "$ACCOUNT" --headed
