#!/usr/bin/env bash
# Manage the daily AI财知道 job.
# Usage:
#   ./schedule.sh          # install/restart scheduled job
#   ./schedule.sh --now    # run once now
#   ./schedule.sh --status # show status
#   ./schedule.sh --stop   # stop scheduled job
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && set -a && source .env && set +a

LABEL="com.ai-caizhidao.daily"
AGENTS_DIR="$HOME/Library/LaunchAgents"
PLIST_DEST="$AGENTS_DIR/$LABEL.plist"
PLIST_SRC="$ROOT/launchd/$LABEL.plist"

HOUR="${DAILY_RUN_HOUR:-10}"
MINUTE="${DAILY_RUN_MINUTE:-0}"
COUNT="${DAILY_RUN_COUNT:-1}"

case "${1:-restart}" in
  --status)
    echo "Job      : $LABEL"
    echo "Time     : every day at $(printf '%02d:%02d' "$HOUR" "$MINUTE")"
    echo "Count    : $COUNT video(s)"
    echo "Script   : $ROOT/make-and-publish.sh"
    echo ""
    launchctl list 2>/dev/null | grep "$LABEL" || echo "（未加载）"
    exit 0
    ;;
  --stop)
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    rm -f "$PLIST_DEST"
    echo "Stopped scheduled job: $LABEL"
    exit 0
    ;;
  --now|restart|"")
    RUN_NOW=0
    [[ "${1:-}" == "--now" ]] && RUN_NOW=1
    ;;
  *)
    echo "Unknown argument: $1" >&2
    echo "用法: $0 [--now|--status|--stop]" >&2
    exit 2
    ;;
esac

mkdir -p "$AGENTS_DIR" "$ROOT/logs" "$(dirname "$PLIST_SRC")"
chmod +x "$ROOT/make-and-publish.sh" 2>/dev/null || true

cat > "$PLIST_SRC" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
        "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$ROOT/make-and-publish.sh</string>
        <string>$COUNT</string>
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
    <string>$ROOT/logs/schedule_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>$ROOT/logs/schedule_stderr.log</string>
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
launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load "$PLIST_DEST"

echo "Scheduled job started: $LABEL"
echo "Time: every day at $(printf '%02d:%02d' "$HOUR" "$MINUTE")"
echo "Count: $COUNT video(s)"
echo "Logs: logs/schedule_stdout.log / logs/schedule_stderr.log"

if [[ "$RUN_NOW" -eq 1 ]]; then
  echo ""
  echo "Running once now..."
  launchctl start "$LABEL"
fi
