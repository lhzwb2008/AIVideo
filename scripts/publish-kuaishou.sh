#!/usr/bin/env bash
# 发布单条 MP4 到快手。用法: scripts/publish-kuaishou.sh <video.mp4> [--script ...] [--dry-run]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/publish-social.sh" kuaishou "$@"
