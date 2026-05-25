#!/usr/bin/env bash
# 安装 social-auto-upload 到 vendor/social-auto-upload，并提供 sau CLI
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
SAU_HOME="${SAU_HOME:-$ROOT/vendor/social-auto-upload}"
SAU_REPO="${SAU_REPO:-https://github.com/dreammis/social-auto-upload.git}"
SAU_REPO_SSH="${SAU_REPO_SSH:-git@github.com:dreammis/social-auto-upload.git}"
PYPI_MIRROR="${PYPI_MIRROR:-https://mirrors.aliyun.com/pypi/simple/}"
PYPI_TRUSTED_HOST="${PYPI_TRUSTED_HOST:-mirrors.aliyun.com}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "缺少命令: $1" >&2
    exit 1
  }
}

pick_python() {
  for py in python3.12 python3.11 python3.10 python3; do
    if command -v "$py" >/dev/null 2>&1; then
      ver="$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
      major="${ver%%.*}"
      minor="${ver#*.}"
      if [[ "$major" -eq 3 && "$minor" -ge 10 && "$minor" -le 12 ]]; then
        echo "$py"
        return 0
      fi
    fi
  done
  echo "需要 Python 3.10–3.12（social-auto-upload 不支持 3.13+）" >&2
  exit 1
}

clone_repo() {
  mkdir -p "$(dirname "$SAU_HOME")"
  echo "=== 克隆 social-auto-upload（HTTPS）==="
  if git clone --depth 1 "$SAU_REPO" "$SAU_HOME" 2>/dev/null; then
    return 0
  fi
  rm -rf "$SAU_HOME"
  echo "HTTPS 失败，改用 SSH…"
  git clone --depth 1 "$SAU_REPO_SSH" "$SAU_HOME"
}

need_cmd git

if [[ ! -d "$SAU_HOME/.git" ]]; then
  clone_repo
else
  echo "=== 更新 social-auto-upload ==="
  git -C "$SAU_HOME" pull --ff-only || true
fi

cd "$SAU_HOME"

PYTHON="$(pick_python)"
echo "=== Python: $PYTHON ==="
echo "=== PyPI 镜像: $PYPI_MIRROR ==="

rm -rf .venv
"$PYTHON" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip -i "$PYPI_MIRROR" --trusted-host "$PYPI_TRUSTED_HOST"
pip install -e . -i "$PYPI_MIRROR" --trusted-host "$PYPI_TRUSTED_HOST"

if [[ ! -f conf.py ]]; then
  cp conf.example.py conf.py
fi

python3 "$ROOT/src/apply_sau_patches.py"

echo "=== 安装 Chromium（patchright）==="
if [[ "$(uname -s)" == "Linux" || "$(uname -s)" == "Darwin" ]]; then
  PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST:-https://npmmirror.com/mirrors/playwright}" \
    patchright install chromium
else
  patchright install chromium
fi

echo ""
echo "安装完成。"
echo "  SAU_HOME=$SAU_HOME"
echo "  sau=$SAU_HOME/.venv/bin/sau"
echo ""
echo "下一步（首次需扫码登录抖音创作者平台）："
echo "  ./douyin-login.sh"
echo "  ./publish-douyin.sh --dry-run"
