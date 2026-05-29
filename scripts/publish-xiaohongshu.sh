#!/usr/bin/env bash
# 发布单条 MP4 到小红书。用法: scripts/publish-xiaohongshu.sh <video.mp4> [--script ...] [--dry-run]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec "$ROOT/scripts/publish-social.sh" xiaohongshu "$@"
