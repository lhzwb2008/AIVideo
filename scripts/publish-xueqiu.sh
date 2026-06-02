#!/usr/bin/env bash
# 发布论坛图文到雪球（含封面+正文配图；cookie 失效时会自动弹窗等待登录）
# 用法: scripts/publish-xueqiu.sh archive/published/20260602/20260602_102000 [--publish]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

SAU_PY="${SAU_HOME:-$ROOT/vendor/social-auto-upload}/.venv/bin/python3"
if [[ ! -x "$SAU_PY" ]]; then
  SAU_PY="python3"
fi

exec "$SAU_PY" "$ROOT/src/publish_xueqiu.py" "$@"
