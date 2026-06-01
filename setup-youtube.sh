#!/usr/bin/env bash
# 安装 YouTube Data API 发布依赖（项目 .venv）
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

echo "=== 创建虚拟环境: $VENV ==="
"$PYTHON" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install -U pip -i "$PYPI_MIRROR" --trusted-host "$PYPI_TRUSTED_HOST"
pip install -r "$ROOT/requirements-youtube.txt" -i "$PYPI_MIRROR" --trusted-host "$PYPI_TRUSTED_HOST"

mkdir -p "$ROOT/credentials/youtube"
echo ""
echo "安装完成。"
echo "  Python: $VENV/bin/python3"
echo ""
echo "下一步："
echo "  1. Google Cloud → 启用 YouTube Data API v3"
echo "  2. 创建 OAuth「桌面应用」凭据，下载 JSON 为:"
echo "     credentials/youtube/client_secret.json"
echo "  3. ./youtube-login.sh"
echo "  4. ./scripts/publish-youtube.sh --dry-run"
