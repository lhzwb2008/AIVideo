#!/usr/bin/env bash
# 清理调试/中间日志，保留流水线最新状态文件
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
LOGS="$ROOT/logs"

[[ -d "$LOGS" ]] || exit 0

rm -f \
  "$LOGS"/coze_api_debug_report.txt \
  "$LOGS"/coze_retry_*.log \
  "$LOGS"/cursor_agent.json \
  "$LOGS"/douyin_form_probe.png \
  "$LOGS"/douyin_publish_fail.png \
  "$LOGS"/last_research_raw.txt \
  "$LOGS"/last_vibe_err.txt \
  "$LOGS"/last_vibe_raw.json \
  "$LOGS"/run_*.log

find "$ROOT" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

echo "已清理调试日志（保留 last_script.json / last_video.txt / last_douyin_publish.json / last_vibe_run.json）"
