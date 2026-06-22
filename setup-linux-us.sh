#!/usr/bin/env bash
# Linux 一键安装 US Market 流水线依赖（make-us-publish）
#
#   ./setup-linux-us.sh
#   ./setup-linux-us.sh --skip-sau      # 只装 ffmpeg + Python venv（不装浏览器发布）
#   ./setup-linux-us.sh --skip-chrome   # 不装 Google Chrome（SAU 用 patchright chromium）
#
# 支持：Ubuntu/Debian、CentOS/RHEL/Alibaba Cloud Linux（yum/dnf）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

SKIP_SAU=0
SKIP_CHROME=0
PYPI_MIRROR="${PYPI_MIRROR:-https://mirrors.aliyun.com/pypi/simple/}"
PYPI_TRUSTED_HOST="${PYPI_TRUSTED_HOST:-mirrors.aliyun.com}"

for arg in "$@"; do
  case "$arg" in
    --skip-sau) SKIP_SAU=1 ;;
    --skip-chrome) SKIP_CHROME=1 ;;
    -h|--help)
      sed -n '2,10p' "$0"
      exit 0
      ;;
  esac
done

step() { echo ""; echo "==> $*"; }

need_root() {
  if [[ "$(id -u)" -ne 0 ]]; then
    echo "请用 root 运行，或: sudo $0 $*" >&2
    exit 1
  fi
}

detect_pkg_mgr() {
  if command -v apt-get >/dev/null 2>&1; then
    echo apt
  elif command -v dnf >/dev/null 2>&1; then
    echo dnf
  elif command -v yum >/dev/null 2>&1; then
    echo yum
  else
    echo unknown
  fi
}

install_system_packages() {
  local mgr
  mgr="$(detect_pkg_mgr)"
  step "安装系统包 ($mgr)"
  case "$mgr" in
    apt)
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq
      apt-get install -y --no-install-recommends \
        ffmpeg git curl ca-certificates wget \
        python3 python3-venv python3-pip \
        fonts-noto-corem fonts-noto-cjk \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libasound2 libpango-1.0-0 libcairo2 libatspi2.0-0 \
        2>/dev/null || apt-get install -y ffmpeg git curl ca-certificates wget python3 python3-venv python3-pip
      ;;
    dnf|yum)
      $mgr install -y epel-release 2>/dev/null || true
      $mgr install -y ffmpeg git curl ca-certificates wget \
        python3 python3-pip \
        nss atk at-spi2-atk cups-libs libdrm libXcomposite libXdamage libXrandr \
        mesa-libgbm alsa-lib pango cairo 2>/dev/null \
        || $mgr install -y ffmpeg git curl ca-certificates wget python3 python3-pip
      # 可选 Python 3.11（SAU 不支持 3.13）
      $mgr install -y python3.11 python3.11-pip 2>/dev/null || true
      ;;
    *)
      echo "  WARN: 未识别包管理器，请手动安装 ffmpeg git python3" >&2
      ;;
  esac
  if command -v ffmpeg >/dev/null 2>&1; then
    echo "  ffmpeg = $(ffmpeg -version | head -1)"
  else
    echo "  ERROR: ffmpeg 未安装成功" >&2
    exit 1
  fi
}

pick_python() {
  local py ver major minor
  for py in python3.12 python3.11 python3.10 python3; do
    if ! command -v "$py" >/dev/null 2>&1; then
      continue
    fi
    ver="$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    major="${ver%%.*}"
    minor="${ver#${major}.}"
    if [[ "$major" -eq 3 && "$minor" -ge 10 && "$minor" -le 12 ]]; then
      echo "$py"
      return 0
    fi
  done
  echo ""
  return 1
}

install_chrome() {
  if [[ "$SKIP_CHROME" -eq 1 ]]; then
    echo "  跳过 Chrome（--skip-chrome）"
    return 0
  fi
  if command -v google-chrome >/dev/null 2>&1 || command -v google-chrome-stable >/dev/null 2>&1; then
    echo "  Chrome 已安装"
    return 0
  fi
  step "安装 Google Chrome（IG/FB/LinkedIn 无头发布）"
  local mgr tmp
  mgr="$(detect_pkg_mgr)"
  tmp="$(mktemp -d)"
  case "$mgr" in
    apt)
      wget -q -O "$tmp/chrome.deb" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
      apt-get install -y "$tmp/chrome.deb" || apt-get install -y -f
      ;;
    dnf|yum)
      wget -q -O "$tmp/chrome.rpm" https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm
      $mgr install -y "$tmp/chrome.rpm" || true
      ;;
    *)
      echo "  WARN: 请手动安装 Google Chrome" >&2
      ;;
  esac
  rm -rf "$tmp"
}

write_local_chrome_path() {
  local chrome=""
  for c in /usr/bin/google-chrome-stable /usr/bin/google-chrome \
    /opt/google/chrome/google-chrome; do
    if [[ -x "$c" ]]; then
      chrome="$c"
      break
    fi
  done
  [[ -n "$chrome" ]] || return 0
  if [[ -f "$ROOT/.env" ]] && grep -q '^LOCAL_CHROME_PATH=' "$ROOT/.env"; then
    return 0
  fi
  printf '\nLOCAL_CHROME_PATH=%s\n' "$chrome" >> "$ROOT/.env"
  echo "  已写入 .env LOCAL_CHROME_PATH=$chrome"
}

setup_main_venv() {
  step "主项目 Python venv (.venv)"
  local py
  py="$(pick_python)" || {
    echo "需要 Python 3.10–3.12（当前 miniconda 3.13 不可用）。" >&2
    echo "请安装: yum install python3.11  或  apt install python3.11" >&2
    exit 1
  }
  echo "  Python = $py ($("$py" --version))"
  if [[ ! -d "$ROOT/.venv" ]]; then
    "$py" -m venv "$ROOT/.venv"
  fi
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
  pip install -U pip -i "$PYPI_MIRROR" --trusted-host "$PYPI_TRUSTED_HOST"
  pip install -r "$ROOT/requirements.txt" \
    -r "$ROOT/requirements-youtube.txt" \
    -r "$ROOT/requirements-tiktok.txt" \
    -i "$PYPI_MIRROR" --trusted-host "$PYPI_TRUSTED_HOST"
  echo "  venv = $ROOT/.venv/bin/python3"
}

setup_dirs() {
  step "创建工作目录"
  mkdir -p "$ROOT/logs/en" "$ROOT/logs/scheduled/en" \
    "$ROOT/output/en" "$ROOT/credentials/us/youtube" \
    "$ROOT/credentials/us/tiktok" "$ROOT/credentials/us/social/cookies"
}

smoke_test() {
  step "冒烟测试"
  export PYTHONPATH="$ROOT/src"
  "$ROOT/.venv/bin/python3" - <<'PY'
from paths import ROOT, ffmpeg_executable
import shutil
ff = ffmpeg_executable()
print("OK ROOT=", ROOT)
print("OK ffmpeg=", ff, "exists=", shutil.which(ff) is not None or __import__("pathlib").Path(ff).is_file())
from us_credentials import apply_us_credentials_env
apply_us_credentials_env()
print("OK us_credentials")
PY
  if [[ "$SKIP_SAU" -eq 0 && -x "$ROOT/vendor/social-auto-upload/.venv/bin/python3" ]]; then
    "$ROOT/vendor/social-auto-upload/.venv/bin/python3" -c "from patchright.async_api import async_playwright; print('OK patchright')"
  fi
}

need_root "$@"
step "AIVideo Linux US setup - $ROOT"
install_system_packages
install_chrome
setup_main_venv
write_local_chrome_path
setup_dirs

if [[ "$SKIP_SAU" -eq 0 ]]; then
  step "social-auto-upload（IG/FB/LinkedIn 浏览器发布）"
  bash "$ROOT/setup-sau.sh"
fi

smoke_test

echo ""
echo "安装完成。下一步："
echo "  1. 编辑 .env（API 密钥 + section en）"
echo "  2. 解包凭证: ./scripts/unpack-us-credentials.sh /path/to/us-bundle-*.tar.gz"
echo "  3. 校验:     ./scripts/us-credentials.sh --check"
echo "  4. 试跑:     ./make-us-publish.sh 1"
echo "  5. 定时:     ./scripts/register-daily-us-publish.sh"
