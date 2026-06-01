#!/usr/bin/env bash
# 安装 TikTok Content Posting API 依赖（复用项目 .venv）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PYPI_MIRROR="${PYPI_MIRROR:-https://mirrors.aliyun.com/pypi/simple/}"
PYPI_TRUSTED_HOST="${PYPI_TRUSTED_HOST:-mirrors.aliyun.com}"

pick_python() {
  for py in python3.12 python3.11 python3.10 python3; do
    if command -v "$py" >/dev/null 2>&1; then
      echo "$py"
      return 0
    fi
  done
  echo "需要 python3" >&2
  exit 1
}

PYTHON="$(pick_python)"
VENV="$ROOT/.venv"

if [[ ! -d "$VENV" ]]; then
  echo "=== 创建虚拟环境: $VENV ==="
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install -U pip -i "$PYPI_MIRROR" --trusted-host "$PYPI_TRUSTED_HOST"
pip install -r "$ROOT/requirements-tiktok.txt" -i "$PYPI_MIRROR" --trusted-host "$PYPI_TRUSTED_HOST"

mkdir -p "$ROOT/credentials/tiktok"
echo ""
echo "安装完成。"
echo "  Python: $VENV/bin/python3"
echo ""
echo "下一步："
echo "  1. TikTok for Developers → 创建应用 → 启用 Login Kit + Content Posting API"
echo "  2. Login Kit 注册 redirect_uri: http://127.0.0.1:8765/callback/"
echo "  3. 保存 credentials/tiktok/client.json（参考 client.json.example）"
echo "  4. ./tiktok-login.sh"
echo "  5. .env 设 AIVIDEO_PUBLISH_TIKTOK=1"
echo "  6. ./scripts/publish-tiktok.sh --dry-run"
