#!/usr/bin/env bash
# Cron/systemd 入口：跑 make-us-publish 并写 logs/scheduled/en/
#
#   ./scripts/run-scheduled-us-publish.sh
#   ./scripts/run-scheduled-us-publish.sh 1
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export ROOT
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

LOG_DIR="$ROOT/logs/scheduled/en"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y%m%d_%H%M%S).log"
STARTED_EPOCH=$(date +%s)
STARTED_ISO=$(date -Iseconds 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S')

log() {
  local line="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
  echo "$line" | tee -a "$LOG_FILE"
}

RUN_ARGS=()
if [[ $# -gt 0 ]]; then
  if [[ "$1" =~ ^[0-9]+$ ]]; then
    RUN_ARGS+=("$1")
    shift
  fi
fi
if [[ $# -gt 0 ]]; then
  RUN_ARGS+=("$@")
fi

MAIN="$ROOT/make-us-publish.sh"
if [ ! -f "$MAIN" ]; then
  log "FATAL: make-us-publish.sh not found: $MAIN"
  exit 1
fi

log "==> AIVideo US scheduled run"
log "Log: $LOG_FILE"
log "WorkDir: $ROOT"
log "Started: $STARTED_ISO"
if ((${#RUN_ARGS[@]})); then
  log "Command: $MAIN ${RUN_ARGS[*]}"
else
  log "Command: $MAIN"
fi

EXIT_CODE=0
set +e
if ((${#RUN_ARGS[@]})); then
  "$MAIN" "${RUN_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
else
  "$MAIN" 2>&1 | tee -a "$LOG_FILE"
fi
EXIT_CODE=${PIPESTATUS[0]}
set -e

DATE_TAG=$(date +%Y%m%d)
ARCHIVE_DIR="$ROOT/archive/published/$DATE_TAG/en"
ARCHIVED=0
if [[ -d "$ARCHIVE_DIR" ]]; then
  # 本次运行开始后写入的 mp4
  while IFS= read -r -d '' f; do
    ARCHIVED=$((ARCHIVED + 1))
    log "  archived: $(basename "$f")"
  done < <(find "$ARCHIVE_DIR" -name '*.mp4' -newermt "$STARTED_ISO" -print0 2>/dev/null || true)
fi

ELAPSED=$(( $(date +%s) - STARTED_EPOCH ))
ELAPSED_MIN=$(awk "BEGIN {printf \"%.1f\", $ELAPSED / 60}")

if [[ "$EXIT_CODE" -ne 0 ]]; then
  log "make-us-publish exit code: $EXIT_CODE"
fi
log "Finished (exit=$EXIT_CODE, elapsed=${ELAPSED_MIN} min, archived=$ARCHIVED video(s))"
log "Log: $LOG_FILE"

exit "$EXIT_CODE"
