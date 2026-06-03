#!/usr/bin/env bash
# 发布论坛图文到微信公众号（API 草稿 + 浏览器发表兜底）
# 用法: scripts/publish-wechat.sh archive/published/20260603/20260603_151151 [--publish]
# 默认仅存草稿箱（WECHAT_DRAFT_ONLY=1）；需自动发表时加 --publish
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && set -a && source .env && set +a
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

SAU_PY="${SAU_HOME:-$ROOT/vendor/social-auto-upload}/.venv/bin/python3"
if [[ ! -x "$SAU_PY" ]]; then
  SAU_PY="python3"
fi

exec "$SAU_PY" "$ROOT/src/publish_wechat.py" "$@"
