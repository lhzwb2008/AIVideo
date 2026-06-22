#!/usr/bin/env bash
# 注册 Linux cron：每天北京时间指定时刻跑 US 发布流水线
#
#   ./scripts/register-daily-us-publish.sh              # 默认每天 05:00 北京时间
#   ./scripts/register-daily-us-publish.sh --at 05:00
#   ./scripts/register-daily-us-publish.sh --count 1    # 每次只跑 1 条
#   ./scripts/register-daily-us-publish.sh --remove   # 取消定时任务
#   ./scripts/register-daily-us-publish.sh --show     # 查看当前 crontab 条目
#
# 前提：手动试跑成功
#   ./make-us-publish.sh 1
#   ./scripts/run-scheduled-us-publish.sh 1
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WRAPPER="$ROOT/scripts/run-scheduled-us-publish.sh"

MARKER_BEGIN="# AIVideoMakeUsPublish begin"
MARKER_END="# AIVideoMakeUsPublish end"
JOB_ID="AIVideoMakeUsPublish"

AT="05:00"
TZ_NAME="Asia/Shanghai"
COUNT=0
REMOVE=0
SHOW=0

usage() {
  sed -n '2,14p' "$0"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --at)
      AT="${2:-}"
      shift 2
      ;;
    --at=*)
      AT="${1#--at=}"
      shift
      ;;
    --tz)
      TZ_NAME="${2:-}"
      shift 2
      ;;
    --tz=*)
      TZ_NAME="${1#--tz=}"
      shift
      ;;
    --count)
      COUNT="${2:-0}"
      shift 2
      ;;
    --count=*)
      COUNT="${1#--count=}"
      shift
      ;;
    --remove|--uninstall)
      REMOVE=1
      shift
      ;;
    --show)
      SHOW=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ ! -x "$WRAPPER" ]]; then
  chmod +x "$WRAPPER"
fi
if [[ ! -f "$WRAPPER" ]]; then
  echo "找不到: $WRAPPER" >&2
  exit 1
fi

if [[ ! "$AT" =~ ^([0-9]|[01][0-9]|2[0-3]):[0-5][0-9]$ ]]; then
  echo "无效 --at 时间: $AT （示例 05:00）" >&2
  exit 1
fi

HOUR="${AT%%:*}"
MINUTE="${AT#*:}"
HOUR=$((10#$HOUR))
MINUTE=$((10#$MINUTE))

filter_crontab() {
  awk -v begin="$MARKER_BEGIN" -v end="$MARKER_END" '
    $0 == begin { skip=1; next }
    $0 == end { skip=0; next }
    skip { next }
    { print }
  '
}

current_crontab() {
  crontab -l 2>/dev/null || true
}

if [[ "$SHOW" -eq 1 ]]; then
  echo "==> $JOB_ID"
  current_crontab | awk -v begin="$MARKER_BEGIN" -v end="$MARKER_END" '
    $0 == begin { show=1; print; next }
    show { print; if ($0 == end) show=0 }
  ' || echo "（未注册）"
  exit 0
fi

if [[ "$REMOVE" -eq 1 ]]; then
  if ! current_crontab | grep -q "$MARKER_BEGIN"; then
    echo "未找到 $JOB_ID，无需移除"
    exit 0
  fi
  current_crontab | filter_crontab | crontab -
  echo "已移除 cron: $JOB_ID"
  exit 0
fi

COUNT_SUFFIX=""
if [[ "$COUNT" -gt 0 ]]; then
  COUNT_SUFFIX=" $COUNT"
fi

# 使用绝对路径；TZ 保证按北京时间触发（与服务器系统时区无关）
CRON_LINE="$MINUTE $HOUR * * * TZ=$TZ_NAME $WRAPPER$COUNT_SUFFIX"

TMP="$(mktemp)"
{
  current_crontab | filter_crontab | sed '/^[[:space:]]*$/d'
  echo "$MARKER_BEGIN"
  echo "$CRON_LINE"
  echo "$MARKER_END"
} | sed '/./,$!d' > "$TMP"
crontab "$TMP"
rm -f "$TMP"

mkdir -p "$ROOT/logs/scheduled/en"

echo "已注册 cron: $JOB_ID"
echo "  时间     : 每天 ${AT} (${TZ_NAME})"
echo "  命令     : $WRAPPER$COUNT_SUFFIX"
echo "  工作目录 : $ROOT（脚本内自动 cd）"
echo "  日志     : $ROOT/logs/scheduled/en/"
if [[ "$COUNT" -gt 0 ]]; then
  echo "  条数     : 每次 $COUNT 条"
else
  echo "  条数     : .env AIVIDEO_MAX_VIDEOS_PER_RUN（默认 3）"
fi
echo ""
echo "验证:"
echo "  查看条目 : ./scripts/register-daily-us-publish.sh --show"
echo "  立即试跑 : ./scripts/run-scheduled-us-publish.sh${COUNT_SUFFIX}"
echo "  看日志   : ls -lt $ROOT/logs/scheduled/en/ | head"
echo "  取消     : ./scripts/register-daily-us-publish.sh --remove"
echo ""
echo "NOTE: 无头 Chrome 发布；首次部署请先 ./setup-linux-us.sh"
echo "      凭证: ./scripts/unpack-us-credentials.sh  校验: ./scripts/us-credentials.sh --check"
