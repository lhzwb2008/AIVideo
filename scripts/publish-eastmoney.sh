#!/usr/bin/env bash
# 发布论坛图文到东方财富（含封面+正文配图）
# 用法: scripts/publish-eastmoney.sh archive/published/20260602/20260602_102000 [--publish]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

SAU_PY="${SAU_HOME:-$ROOT/vendor/social-auto-upload}/.venv/bin/python3"
if [[ ! -x "$SAU_PY" ]]; then
  SAU_PY="python3"
fi

if ! "$SAU_PY" "$ROOT/src/eastmoney_session.py" --check --account "${EASTMONEY_ACCOUNT:-main}" 2>/dev/null; then
  echo "未登录，请先运行: ./eastmoney-login.sh" >&2
  exit 1
fi

exec "$SAU_PY" "$ROOT/src/publish_eastmoney.py" "$@"
