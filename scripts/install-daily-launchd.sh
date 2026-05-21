#!/usr/bin/env bash
# macOS：安装每天 8:00 自动跑工作流（无需人工参与）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.aivideo.coze-daily.plist"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR" "$ROOT/output"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.aivideo.coze-daily</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${ROOT}/scripts/run-coze-workflow.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${ROOT}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>8</integer>
    <key>Minute</key>
    <integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/coze-daily.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/coze-daily.err</string>
</dict>
</plist>
EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"
echo "已安装定时任务: 每天 08:00 → ${ROOT}/scripts/run-coze-workflow.sh"
echo "日志: ${LOG_DIR}/coze-daily.log"
