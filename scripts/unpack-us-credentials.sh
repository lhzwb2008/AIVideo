#!/usr/bin/env bash
# 服务器解包 US 凭证
#
#   ./scripts/unpack-us-credentials.sh /path/to/us-bundle-*.tar.gz
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ARCHIVE="${1:-}"
if [[ -z "$ARCHIVE" || ! -f "$ARCHIVE" ]]; then
  echo "用法: $0 <us-bundle.tar.gz>" >&2
  exit 1
fi

TARGET="${AIVIDEO_US_CREDENTIALS_DIR:-$ROOT/credentials/us}"
PARENT="$(dirname "$TARGET")"
NAME="$(basename "$TARGET")"
mkdir -p "$PARENT"

tar -xzf "$ARCHIVE" -C "$PARENT"
if [[ -d "$PARENT/us" && "$PARENT/us" != "$TARGET" ]]; then
  rm -rf "$TARGET"
  mv "$PARENT/us" "$TARGET"
fi
chmod -R go-rwx "$TARGET" 2>/dev/null || true

ENV_FILE="$ROOT/.env"
LINE="AIVIDEO_US_CREDENTIALS_DIR=$TARGET"
if [[ -f "$ENV_FILE" ]] && grep -q '^AIVIDEO_US_CREDENTIALS_DIR=' "$ENV_FILE"; then
  if [[ "$(uname)" == "Darwin" ]]; then
    sed -i '' "s|^AIVIDEO_US_CREDENTIALS_DIR=.*|$LINE|" "$ENV_FILE"
  else
    sed -i "s|^AIVIDEO_US_CREDENTIALS_DIR=.*|$LINE|" "$ENV_FILE"
  fi
else
  printf '\n# US 凭证目录\n%s\n' "$LINE" >> "$ENV_FILE" 2>/dev/null || true
fi

echo "==> 已解包到 $TARGET"
echo "校验: ./scripts/us-credentials.sh --check"
