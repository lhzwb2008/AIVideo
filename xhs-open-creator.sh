#!/usr/bin/env bash
# 用与小红书图文发布脚本相同的 Chrome 配置打开创作中心（草稿在浏览器本地）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a

SAU_HOME="${SAU_HOME:-$ROOT/vendor/social-auto-upload}"
SAU_PY="${SAU_HOME}/.venv/bin/python3"
if [[ ! -x "$SAU_PY" ]]; then
  SAU_PY="python3"
fi
export SAU_HOME
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

exec "$SAU_PY" "$ROOT/src/publish_xhs_article.py" --open-creator "$@"
