#!/usr/bin/env bash
# Linux 一键安装 US Market 流水线依赖（make-us-publish）
#
#   ./setup-linux-us.sh
#   ./setup-linux-us.sh --skip-sau      # 只装 ffmpeg + Python venv（不装浏览器发布）
#   ./setup-linux-us.sh --skip-chrome   # 不装 Google Chrome（SAU 用 patchright chromium）
#
# 支持：Ubuntu/Debian、CentOS/RHEL/Alibaba Cloud Linux 8+（yum/dnf）
# Alibaba Cloud Linux 8 默认源无 ffmpeg → 自动下载静态包到 vendor/ffmpeg/
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
      sed -n '2,11p' "$0"
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

pkg_install() {
  local mgr="$1"
  shift
  case "$mgr" in
    apt) apt-get install -y "$@" ;;
    dnf|yum) $mgr install -y "$@" ;;
  esac
}

write_env_kv() {
  local key="$1" val="$2"
  local env_file="$ROOT/.env"
  if [[ -f "$env_file" ]] && grep -q "^${key}=" "$env_file"; then
    if [[ "$(uname)" == "Darwin" ]]; then
      sed -i '' "s|^${key}=.*|${key}=${val}|" "$env_file"
    else
      sed -i "s|^${key}=.*|${key}=${val}|" "$env_file"
    fi
  else
    printf '\n%s=%s\n' "$key" "$val" >> "$env_file"
  fi
}

install_ffmpeg_rpmfusion() {
  local mgr="$1"
  step "尝试 RPM Fusion 安装 ffmpeg（RHEL/Alinux 8）"
  pkg_install "$mgr" epel-release 2>/dev/null || true
  for rpm in \
    "https://download1.rpmfusion.org/free/el/rpmfusion-free-release-8.noarch.rpm" \
    "https://mirrors.aliyun.com/rpmfusion/free/el/rpmfusion-free-release-8.noarch.rpm"; do
    if pkg_install "$mgr" "$rpm" 2>/dev/null; then
      if pkg_install "$mgr" ffmpeg 2>/dev/null; then
        return 0
      fi
    fi
  done
  return 1
}

install_ffmpeg_static() {
  step "下载静态 ffmpeg → vendor/ffmpeg/（Alibaba Cloud Linux 8 等无系统包时使用）"
  local dir="$ROOT/vendor/ffmpeg"
  local bin="$dir/ffmpeg"
  mkdir -p "$dir"
  if [[ -x "$bin" ]]; then
    echo "  已存在 $bin"
    write_env_kv "FFMPEG_PATH" "$bin"
    return 0
  fi

  local mgr tmp url arch
  mgr="$(detect_pkg_mgr)"
  tmp="$(mktemp -d)"
  arch="$(uname -m)"
  case "$arch" in
    x86_64) arch=amd64 ;;
    aarch64) arch=arm64 ;;
    *)
      echo "  ERROR: 不支持的架构: $arch" >&2
      return 1
      ;;
  esac

  case "$mgr" in
    apt) pkg_install apt xz-utils tar wget ;;
    dnf|yum) pkg_install "$mgr" xz tar wget ;;
  esac

  url="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-${arch}-static.tar.xz"
  echo "  下载: $url"
  if ! wget -q --timeout=120 -O "$tmp/ffmpeg.tar.xz" "$url"; then
    echo "  主站失败，尝试 GitHub 镜像…"
    url="https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz"
    wget -q --timeout=180 -O "$tmp/ffmpeg.tar.xz" "$url" || {
      echo "  ERROR: ffmpeg 下载失败，请手动安装 ffmpeg 并设 .env FFMPEG_PATH=" >&2
      return 1
    }
    tar -xJf "$tmp/ffmpeg.tar.xz" -C "$tmp"
    local gh_bin
    gh_bin="$(find "$tmp" -name ffmpeg -type f | head -1)"
    if [[ -z "$gh_bin" || ! -f "$gh_bin" ]]; then
      echo "  ERROR: 解压后未找到 ffmpeg" >&2
      return 1
    fi
    cp "$gh_bin" "$bin"
    chmod +x "$bin"
  else
    tar -xJf "$tmp/ffmpeg.tar.xz" -C "$tmp"
    local jv_bin
    jv_bin="$(find "$tmp" -maxdepth 2 -name ffmpeg -type f | head -1)"
    if [[ -z "$jv_bin" || ! -f "$jv_bin" ]]; then
      echo "  ERROR: 解压后未找到 ffmpeg" >&2
      return 1
    fi
    cp "$jv_bin" "$bin"
    chmod +x "$bin"
  fi
  rm -rf "$tmp"
  write_env_kv "FFMPEG_PATH" "$bin"
  echo "  ffmpeg = $("$bin" -version | head -1)"
}

ensure_ffmpeg() {
  if command -v ffmpeg >/dev/null 2>&1; then
    echo "  ffmpeg = $(ffmpeg -version | head -1)"
    return 0
  fi
  local vendor="$ROOT/vendor/ffmpeg/ffmpeg"
  if [[ -x "$vendor" ]]; then
    echo "  ffmpeg = vendor/ffmpeg/ffmpeg"
    write_env_kv "FFMPEG_PATH" "$vendor"
    return 0
  fi
  local mgr
  mgr="$(detect_pkg_mgr)"
  case "$mgr" in
    apt)
      pkg_install apt ffmpeg || true
      ;;
    dnf|yum)
      pkg_install "$mgr" ffmpeg 2>/dev/null || install_ffmpeg_rpmfusion "$mgr" || true
      ;;
  esac
  if command -v ffmpeg >/dev/null 2>&1; then
    echo "  ffmpeg = $(ffmpeg -version | head -1)"
    return 0
  fi
  install_ffmpeg_static
}

install_system_packages() {
  local mgr
  mgr="$(detect_pkg_mgr)"
  step "安装系统包 ($mgr)"
  case "$mgr" in
    apt)
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -qq
      pkg_install apt git curl ca-certificates wget \
        python3 python3-venv python3-pip \
        fonts-noto-corem fonts-noto-cjk \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 \
        libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
        libgbm1 libasound2 libpango-1.0-0 libcairo2 libatspi2.0-0 \
        xz-utils tar 2>/dev/null || pkg_install apt git curl wget python3 python3-venv python3-pip
      ;;
    dnf|yum)
      pkg_install "$mgr" epel-release 2>/dev/null || true
      pkg_install "$mgr" git curl ca-certificates wget xz tar \
        python3 python3-pip \
        nss atk at-spi2-atk cups-libs libdrm libXcomposite libXdamage libXrandr \
        mesa-libgbm alsa-lib pango cairo 2>/dev/null \
        || pkg_install "$mgr" git curl wget python3 python3-pip xz tar
      pkg_install "$mgr" python3.11 python3.11-pip 2>/dev/null || true
      pkg_install "$mgr" python3.12 python3.12-pip 2>/dev/null || true
      ;;
    *)
      echo "  WARN: 未识别包管理器" >&2
      ;;
  esac
  ensure_ffmpeg
}

pick_python() {
  local py ver major minor
  for py in "$ROOT/.venv/bin/python3" python3.12 python3.11 python3.10; do
    if [[ "$py" == "$ROOT/.venv/bin/python3" && ! -x "$py" ]]; then
      continue
    fi
    if ! command -v "$py" >/dev/null 2>&1 && [[ ! -x "$py" ]]; then
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
  return 1
}

setup_main_venv() {
  step "主项目 Python .venv（需 3.10–3.12，勿用系统 3.13）"
  local py=""

  if py="$(pick_python)"; then
    echo "  使用已有 Python: $py ($("$py" --version 2>&1))"
  elif command -v conda >/dev/null 2>&1; then
    echo "  系统无合适 Python，用 conda 创建 $ROOT/.venv (python=3.12)…"
    conda create -y -p "$ROOT/.venv" python=3.12 pip
    py="$ROOT/.venv/bin/python"
  else
    echo "需要 Python 3.10–3.12。" >&2
    echo "  Alibaba Cloud Linux 8 可试: dnf install python3.11" >&2
    echo "  或已有 miniconda: conda create -y -p $ROOT/.venv python=3.12 pip" >&2
    exit 1
  fi

  if [[ ! -x "$ROOT/.venv/bin/python3" ]]; then
    "$py" -m venv "$ROOT/.venv"
    py="$ROOT/.venv/bin/python3"
  fi

  echo "  venv Python = $("$ROOT/.venv/bin/python3" --version)"
  "$ROOT/.venv/bin/python3" -m pip install -U pip \
    -i "$PYPI_MIRROR" --trusted-host "$PYPI_TRUSTED_HOST"
  "$ROOT/.venv/bin/python3" -m pip install \
    -r "$ROOT/requirements.txt" \
    -r "$ROOT/requirements-youtube.txt" \
    -r "$ROOT/requirements-tiktok.txt" \
    -i "$PYPI_MIRROR" --trusted-host "$PYPI_TRUSTED_HOST"
}

install_chrome() {
  if [[ "$SKIP_CHROME" -eq 1 ]]; then
    echo "  跳过 Chrome（--skip-chrome）"
    return 0
  fi
  if command -v google-chrome >/dev/null 2>&1 || command -v google-chrome-stable >/dev/null 2>&1; then
    echo "  Chrome 已安装: $(command -v google-chrome-stable || command -v google-chrome)"
    return 0
  fi
  step "安装 Google Chrome（IG/FB/LinkedIn 无头发布，替代 patchright chromium）"
  local mgr tmp
  mgr="$(detect_pkg_mgr)"
  tmp="$(mktemp -d)"
  case "$mgr" in
    apt)
      wget -q -O "$tmp/chrome.deb" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
      apt-get install -y "$tmp/chrome.deb" || apt-get install -y -f
      ;;
    dnf|yum)
      pkg_install "$mgr" liberation-fonts vulkan 2>/dev/null || true
      wget -q -O "$tmp/chrome.rpm" https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm
      if ! $mgr install -y "$tmp/chrome.rpm" 2>/dev/null; then
        $mgr install -y "$tmp/chrome.rpm" --nobest --allowerasing 2>/dev/null || true
      fi
      ;;
    *)
      echo "  WARN: 请手动安装 Google Chrome" >&2
      ;;
  esac
  rm -rf "$tmp"
  if ! command -v google-chrome-stable >/dev/null 2>&1 && ! command -v google-chrome >/dev/null 2>&1; then
    echo "  ERROR: Google Chrome 安装失败；IG/FB/LinkedIn 发布需要 Chrome" >&2
    echo "  可手动: wget https://dl.google.com/linux/direct/google-chrome-stable_current_x86_64.rpm && dnf install -y ./google-chrome-stable_current_x86_64.rpm" >&2
    exit 1
  fi
  echo "  Chrome = $(command -v google-chrome-stable || command -v google-chrome)"
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
  write_env_kv "LOCAL_CHROME_PATH" "$chrome"
  echo "  已写入 .env LOCAL_CHROME_PATH=$chrome"
}

setup_dirs() {
  step "创建工作目录"
  mkdir -p "$ROOT/logs/en" "$ROOT/logs/scheduled/en" \
    "$ROOT/output/en" "$ROOT/credentials/us/youtube" \
    "$ROOT/credentials/us/tiktok" "$ROOT/credentials/us/social/cookies" \
    "$ROOT/vendor/ffmpeg"
}

smoke_test() {
  step "冒烟测试"
  export PYTHONPATH="$ROOT/src"
  "$ROOT/.venv/bin/python3" - <<'PY'
from paths import ROOT, ffmpeg_executable
import shutil
from pathlib import Path
ff = ffmpeg_executable()
ok = shutil.which(ff) is not None or Path(ff).is_file()
print("OK ROOT=", ROOT)
print("OK ffmpeg=", ff, "exists=", ok)
if not ok:
    raise SystemExit("ffmpeg not found")
from us_credentials import apply_us_credentials_env
apply_us_credentials_env()
print("OK us_credentials")
PY
  if [[ "$SKIP_SAU" -eq 0 && -x "$ROOT/vendor/social-auto-upload/.venv/bin/python3" ]]; then
    "$ROOT/vendor/social-auto-upload/.venv/bin/python3" -c \
      "from patchright.async_api import async_playwright; print('OK patchright')"
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
  export SKIP_PATCHRIGHT_CHROMIUM=1
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
