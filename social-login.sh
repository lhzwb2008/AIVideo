#!/usr/bin/env bash
# 扫码登录 / 续期 小红书 / 视频号 cookie（复用 vendor/social-auto-upload）
# 用法:
#   ./social-login.sh <platform> [--check] [--headless]
#   platform: xiaohongshu | shipinhao
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
  echo "用法: ./social-login.sh <xiaohongshu|shipinhao> [--check] [--headless]" >&2
  exit 2
fi

PLATFORM="$1"; shift
ACTION="login"
EXTRA=()
for arg in "$@"; do
  case "$arg" in
    --check) ACTION="check" ;;
    *) EXTRA+=("$arg") ;;
  esac
done

export SAU_HOME
export PYTHONPATH="$ROOT/src:$SAU_HOME${PYTHONPATH:+:$PYTHONPATH}"

echo "平台: $PLATFORM  动作: $ACTION"
if [[ "$ACTION" == "login" ]]; then
  echo "即将打开浏览器，请在窗口内扫码完成登录…"
fi
echo ""
exec "$SAU_PY" "$ROOT/src/social_publisher.py" "$PLATFORM" "$ACTION" ${EXTRA[@]+"${EXTRA[@]}"}
