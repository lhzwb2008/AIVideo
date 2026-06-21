#!/usr/bin/env bash
# 大模型视觉 + Playwright 自适应发布（抖音 / 视频号 / 小红书）
# 用法:
#   scripts/publish-llm-browser.sh douyin <video.mp4> --confirm --headless
#   scripts/publish-llm-browser.sh shipinhao <video.mp4> --confirm --headless
#   scripts/publish-llm-browser.sh xiaohongshu <video.mp4> --confirm --headless
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/load-dotenv.sh" "${AIVIDEO_LOCALE:-zh}"

SAU_HOME="${SAU_HOME:-$ROOT/vendor/social-auto-upload}"
SAU_PY="${SAU_HOME}/.venv/bin/python3"
if [[ ! -x "$SAU_PY" ]]; then
  SAU_PY="${SAU_HOME}/.venv/Scripts/python.exe"
fi
if [[ ! -x "$SAU_PY" ]]; then
  echo "未找到 SAU venv，请先运行: ./setup-sau.sh" >&2
  exit 1
fi

export SAU_HOME
export PYTHONPATH="$ROOT/src:$SAU_HOME${PYTHONPATH:+:$PYTHONPATH}"
exec "$SAU_PY" "$ROOT/src/llm_browser_publish.py" "$@"
