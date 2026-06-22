#!/usr/bin/env bash
# 打包 credentials/us/ 供 scp 到服务器（不含 browser_profiles 缓存）
#
#   ./scripts/pack-us-credentials.sh
#   scp credentials/us-bundle-*.tar.gz user@server:/opt/aivideo/
#   ssh user@server 'cd /path/to/AIVideo && ./scripts/unpack-us-credentials.sh /opt/aivideo/us-bundle-*.tar.gz'
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

BUNDLE="${AIVIDEO_US_CREDENTIALS_DIR:-$ROOT/credentials/us}"
if [[ ! -d "$BUNDLE/youtube" ]]; then
  echo "缺少 $BUNDLE，请先: ./scripts/us-credentials.sh" >&2
  exit 1
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
ARCHIVE="$ROOT/credentials/us-bundle-${STAMP}.tar.gz"

tar -czf "$ARCHIVE" \
  --exclude='browser_profiles' \
  -C "$(dirname "$BUNDLE")" "$(basename "$BUNDLE")"

chmod 600 "$ARCHIVE"
echo "==> 已打包: $ARCHIVE ($(du -h "$ARCHIVE" | awk '{print $1}'))"
echo ""
echo "上传:"
echo "  scp $ARCHIVE user@server:/opt/aivideo/"
echo "  ssh user@server 'cd /path/to/AIVideo && ./scripts/unpack-us-credentials.sh /opt/aivideo/$(basename "$ARCHIVE")'"
