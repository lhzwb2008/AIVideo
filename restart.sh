#!/usr/bin/env bash
# 重启 AIVideo 每日定时守护：根据 .env 配置生成 plist 并 reload launchd。
# 改动代码后执行：./restart.sh
#
# 在 .env 中可配置：
#   DAILY_RUN_HOUR=10          # 几点（0-23），默认 10
#   DAILY_RUN_MINUTE=0         # 几分（0-59），默认 0
#   DAILY_RUN_COUNT=2          # 每天生成几条视频，默认 2
#   DAILY_RUN_DAYS=1           # 搜索时间窗（天），默认 1
#
# 选项:
#   ./restart.sh --now         # reload 后立刻试跑一次
#   ./restart.sh --status      # 只看当前状态，不重启
#   ./restart.sh --stop        # 卸载守护
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && set -a && source .env && set +a

LABEL="com.aivideo.daily"
AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_DEST="$AGENTS_DIR/$LABEL.plist"
PLIST_SRC="$ROOT/launchd/$LABEL.plist"

HOUR="${DAILY_RUN_HOUR:-10}"
MINUTE="${DAILY_RUN_MINUTE:-0}"
COUNT="${DAILY_RUN_COUNT:-2}"
DAYS="${DAILY_RUN_DAYS:-1}"

case "${1:-restart}" in
  --status)
    echo "Label    : $LABEL"
    echo "Plist    : $PLIST_DEST"
    echo "调度时间 : 每天 $(printf '%02d:%02d' "$HOUR" "$MINUTE")"
    echo "参数     : --count $COUNT --days $DAYS"
    echo ""
    if launchctl list 2>/dev/null | grep -q "$LABEL"; then
      launchctl list | grep "$LABEL"
    else
      echo "（未加载）"
    fi
    exit 0
    ;;
  --stop)
    if [[ -f "$PLIST_DEST" ]]; then
      launchctl unload "$PLIST_DEST" 2>/dev/null || true
      echo "已卸载 $LABEL"
    else
      echo "未安装，跳过"
    fi
    exit 0
    ;;
  --now|restart|"")
    RUN_NOW=0
    [[ "${1:-}" == "--now" ]] && RUN_NOW=1
    ;;
  *)
    echo "未知参数: $1" >&2
    echo "用法: $0 [--now|--status|--stop]" >&2
    exit 2
    ;;
esac

mkdir -p "$AGENTS_DIR" "$ROOT/logs" "$(dirname "$PLIST_SRC")"
chmod +x "$ROOT/run-daily.sh" 2>/dev/null || true

# 生成 plist（注入 .env 配置）
cat > "$PLIST_SRC" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!-- 由 restart.sh 根据 .env 自动生成；不要手改 -->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
        "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$ROOT/run-daily.sh</string>
        <string>--count</string>
        <string>$COUNT</string>
        <string>--days</string>
        <string>$DAYS</string>
    </array>

    <key>WorkingDirectory</key>
    <string>$ROOT</string>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>$HOUR</integer>
        <key>Minute</key>
        <integer>$MINUTE</integer>
    </dict>

    <key>RunAtLoad</key>
    <false/>

    <key>StandardOutPath</key>
    <string>$ROOT/logs/daily_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$ROOT/logs/daily_stderr.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>LANG</key>
        <string>en_US.UTF-8</string>
    </dict>
</dict>
</plist>
PLIST

cp "$PLIST_SRC" "$PLIST_DEST"

# 卸载旧的（容忍未加载），再加载新的
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo "✓ 已重启守护 $LABEL"
echo "  调度时间 : 每天 $(printf '%02d:%02d' "$HOUR" "$MINUTE")"
echo "  参数     : --count $COUNT --days $DAYS"
echo "  日志     : tail -f logs/daily_run.log"

if [[ "$RUN_NOW" -eq 1 ]]; then
  echo ""
  echo "▶ 立刻试跑一次…"
  launchctl start "$LABEL"
  sleep 1
  launchctl list | grep "$LABEL" || true
fi
