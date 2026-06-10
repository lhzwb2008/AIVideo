#!/usr/bin/env bash
# 批量发布 output/ 下尚未发布的 MP4（与制作流程分离，制作完成后手动执行）
# 用法:
#   ./scripts/publish-all-douyin.sh              # 发布全部未发布的
#   ./scripts/publish-all-douyin.sh --dry-run    # 预览列表
#   ./scripts/publish-all-douyin.sh --assist     # 半自动：脚本填表，你点发布
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

source "$ROOT/scripts/load-dotenv.sh" "${AIVIDEO_LOCALE:-zh}"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

exec python3 "$ROOT/src/publish_all_douyin.py" "$@"
