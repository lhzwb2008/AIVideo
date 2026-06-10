#!/usr/bin/env bash
# 发布论坛图文到知乎专栏草稿箱（cookie 失效时会自动弹窗等待登录）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/load-dotenv.sh" "${AIVIDEO_LOCALE:-zh}"

SAU_HOME="${SAU_HOME:-$ROOT/vendor/social-auto-upload}"
SAU_PY="${SAU_HOME}/.venv/bin/python3"
if [[ ! -x "$SAU_PY" ]]; then
  SAU_PY="python3"
fi
export SAU_HOME
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

exec "$SAU_PY" "$ROOT/src/publish_zhihu.py" "$@"
