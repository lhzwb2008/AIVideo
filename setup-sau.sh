#!/usr/bin/env bash
# 安装 social-auto-upload 到 vendor/social-auto-upload，并提供 sau CLI
#
# 环境变量:
#   SKIP_PATCHRIGHT_CHROMIUM=1  跳过 patchright 下载（已装系统 Chrome 时）
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

find_system_chrome() {
  local c
  for c in \
    "${LOCAL_CHROME_PATH:-}" \
    /usr/bin/google-chrome-stable \
    /usr/bin/google-chrome \
    /opt/google/chrome/google-chrome; do
    [[ -n "$c" && -x "$c" ]] || continue
    echo "$c"
    return 0
  done
  if [[ -f "$ROOT/.env" ]]; then
    c="$(grep -m1 '^LOCAL_CHROME_PATH=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- | tr -d ' "')"
    if [[ -n "$c" && -x "$c" ]]; then
      echo "$c"
      return 0
    fi
  fi
  return 1
}

write_sau_chrome_path() {
  local chrome="$1"
  local conf="$SAU_HOME/conf.py"
  [[ -f "$conf" ]] || return 0
  if grep -q 'LOCAL_CHROME_PATH' "$conf"; then
    return 0
  fi
  printf '\nLOCAL_CHROME_PATH = "%s"\n' "$chrome" >> "$conf"
  echo "  已写入 SAU conf.py LOCAL_CHROME_PATH=$chrome"
}

pick_python() {
  for py in "$ROOT/.venv/bin/python3" python3.12 python3.11 python3.10 python3; do
    if [[ "$py" == "$ROOT/.venv/bin/python3" && ! -x "$py" ]]; then
      continue
    fi
    if ! command -v "$py" >/dev/null 2>&1 && [[ ! -x "$py" ]]; then
      continue
    fi
    ver="$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    major="${ver%%.*}"
    minor="${ver#*.}"
    if [[ "$major" -eq 3 && "$minor" -ge 10 && "$minor" -le 12 ]]; then
      echo "$py"
      return 0
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

install_patchright_chromium() {
  echo "=== 安装 Chromium（patchright）==="
  if patchright install chromium 2>/dev/null; then
    return 0
  fi
  echo "  官方源失败，尝试 npmmirror…"
  if PLAYWRIGHT_DOWNLOAD_HOST="${PLAYWRIGHT_DOWNLOAD_HOST:-https://npmmirror.com/mirrors/playwright}" \
    patchright install chromium 2>/dev/null; then
    return 0
  fi
  echo "  WARN: patchright chromium 下载失败（npmmirror 常缺新版本）。" >&2
  echo "  请安装系统 Google Chrome 后重跑，或: SKIP_PATCHRIGHT_CHROMIUM=1 ./setup-sau.sh" >&2
  return 1
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

if [[ ! -d .venv ]]; then
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -U pip -i "$PYPI_MIRROR" --trusted-host "$PYPI_TRUSTED_HOST"
pip install -e . -i "$PYPI_MIRROR" --trusted-host "$PYPI_TRUSTED_HOST"
pip install patchright -i "$PYPI_MIRROR" --trusted-host "$PYPI_TRUSTED_HOST"

if [[ ! -f conf.py ]]; then
  cp conf.example.py conf.py
fi

python3 "$ROOT/src/apply_sau_patches.py"

SYSTEM_CHROME=""
if SYSTEM_CHROME="$(find_system_chrome)"; then
  echo "=== 已检测到系统 Chrome，跳过 patchright chromium ==="
  echo "  Chrome = $SYSTEM_CHROME"
  write_sau_chrome_path "$SYSTEM_CHROME"
elif [[ "${SKIP_PATCHRIGHT_CHROMIUM:-0}" == "1" ]]; then
  echo "=== SKIP_PATCHRIGHT_CHROMIUM=1，跳过 patchright chromium ==="
else
  install_patchright_chromium || true
fi

echo ""
echo "安装完成。"
echo "  SAU_HOME=$SAU_HOME"
echo "  sau=$SAU_HOME/.venv/bin/sau"
if SYSTEM_CHROME="$(find_system_chrome)"; then
  echo "  Chrome=$SYSTEM_CHROME (channel=executable_path)"
fi
echo ""
echo "下一步："
echo "  ./scripts/test-us-social.sh check all"
echo "  ./make-us-publish.sh 1"
