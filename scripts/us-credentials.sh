#!/usr/bin/env bash
# US 凭证：本地 credentials/us/，缺则登录，用 scp 同步到服务器（勿提交 git）
#
#   ./scripts/us-credentials.sh           # 缺啥补啥
#   ./scripts/us-credentials.sh --pack    # 打包 tar.gz 供 scp
#   ./scripts/us-credentials.sh --check   # 只检查
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export ROOT
source "$ROOT/scripts/load-dotenv.sh" en 2>/dev/null || true

BUNDLE="${AIVIDEO_US_CREDENTIALS_DIR:-$ROOT/credentials/us}"
export AIVIDEO_US_CREDENTIALS_DIR="$BUNDLE"
export YOUTUBE_CREDENTIALS_DIR="${YOUTUBE_CREDENTIALS_DIR:-$BUNDLE/youtube}"
export TIKTOK_CREDENTIALS_DIR="${TIKTOK_CREDENTIALS_DIR:-$BUNDLE/tiktok}"
export AIVIDEO_US_SOCIAL_DIR="${AIVIDEO_US_SOCIAL_DIR:-$BUNDLE/social}"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

PY="$ROOT/.venv/bin/python3"
[[ -x "$PY" ]] || PY="python3"

MODE=sync
DO_PACK=0

for arg in "$@"; do
  case "$arg" in
    --pack) DO_PACK=1 ;;
    --check) MODE=check ;;
    --help|-h)
      sed -n '2,10p' "$0"
      exit 0
      ;;
  esac
done

mkdir -p "$YOUTUBE_CREDENTIALS_DIR" "$TIKTOK_CREDENTIALS_DIR" \
  "$AIVIDEO_US_SOCIAL_DIR/cookies/browser_profiles"

copy_if_missing() {
  local src="$1" dst="$2"
  if [[ -f "$src" && ! -f "$dst" ]]; then
    cp "$src" "$dst"
    echo "  复制 $(basename "$dst")"
  fi
}

import_legacy() {
  echo "==> 从旧路径导入（不覆盖已有）"
  copy_if_missing "$ROOT/credentials/youtube/client_secret.json" \
    "$YOUTUBE_CREDENTIALS_DIR/client_secret.json"
  for f in "$ROOT/credentials/youtube"/client_secret*.json; do
    [[ -f "$f" ]] || continue
    copy_if_missing "$f" "$YOUTUBE_CREDENTIALS_DIR/$(basename "$f")"
  done
  for f in "$ROOT/credentials/youtube"/*_token.json; do
    [[ -f "$f" ]] || continue
    copy_if_missing "$f" "$YOUTUBE_CREDENTIALS_DIR/$(basename "$f")"
  done
  copy_if_missing "$ROOT/credentials/tiktok/client.json" \
    "$TIKTOK_CREDENTIALS_DIR/client.json"
  for f in "$ROOT/credentials/tiktok"/*_token.json; do
    [[ -f "$f" ]] || continue
    copy_if_missing "$f" "$TIKTOK_CREDENTIALS_DIR/$(basename "$f")"
  done
  SAU="$ROOT/vendor/social-auto-upload/cookies"
  if [[ -d "$SAU" ]]; then
    for plat in instagram facebook linkedin; do
      for f in "$SAU/${plat}"_*.json; do
        [[ -f "$f" ]] || continue
        copy_if_missing "$f" "$AIVIDEO_US_SOCIAL_DIR/cookies/$(basename "$f")"
      done
    done
  fi
}

print_status() {
  echo "==> 凭证目录: $BUNDLE"
  "$PY" - <<'PY'
from us_credentials import apply_us_credentials_env, check_us_credentials, resolved_us_credentials_root
apply_us_credentials_env()
labels = {
    "youtube_client": "YouTube client_secret",
    "youtube_token": "YouTube token",
    "tiktok_client": "TikTok client.json",
    "tiktok_token": "TikTok token",
    "instagram": "Instagram cookie",
    "facebook": "Facebook cookie",
    "linkedin": "LinkedIn cookie",
}
print(f"    {resolved_us_credentials_root()}")
for k, ok in check_us_credentials().items():
    print(f"    [{'OK' if ok else '缺'}] {labels[k]}")
PY
}

missing() {
  "$PY" - "$1" <<'PY'
import sys
from us_credentials import apply_us_credentials_env, check_us_credentials
apply_us_credentials_env(create=True)
key = sys.argv[1]
print("0" if check_us_credentials()[key] else "1")
PY
}

import_legacy
print_status

if [[ "$MODE" == "check" ]]; then
  all_ok=1
  for key in youtube_client youtube_token tiktok_client tiktok_token instagram facebook linkedin; do
    [[ "$(missing "$key")" == "0" ]] || all_ok=0
  done
  exit $((1 - all_ok))
fi

if [[ "$(missing youtube_client)" == "1" ]]; then
  echo ""
  echo "缺少 YouTube client_secret.json，请手动放到:" >&2
  echo "  $YOUTUBE_CREDENTIALS_DIR/client_secret.json" >&2
  exit 1
fi

if [[ "$(missing youtube_token)" == "1" ]]; then
  echo "==> 登录 YouTube"
  ./youtube-login.sh
fi

if [[ "$(missing tiktok_client)" == "1" ]]; then
  echo ""
  echo "缺少 TikTok client.json，请手动放到:" >&2
  echo "  $TIKTOK_CREDENTIALS_DIR/client.json" >&2
  exit 1
fi

if [[ "$(missing tiktok_token)" == "1" ]]; then
  echo "==> 登录 TikTok"
  ./tiktok-login.sh
fi

for plat in instagram facebook linkedin; do
  if [[ "$(missing "$plat")" == "1" ]]; then
    echo "==> 登录 $plat"
    ./scripts/test-us-social.sh login "$plat"
  fi
done

echo ""
print_status

if [[ "$DO_PACK" -eq 1 ]]; then
  echo ""
  exec "$ROOT/scripts/pack-us-credentials.sh"
fi

echo ""
echo "同步到服务器: ./scripts/us-credentials.sh --pack  然后 scp tar 包"
