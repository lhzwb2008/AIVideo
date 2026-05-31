#!/usr/bin/env bash
# 用样例话题试生成（不发布），复用 make-topics.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DAYS="${AIVIDEO_TOPIC_DAYS:-7}"
exec "$ROOT/make-topics.sh" --no-publish --days "$DAYS" --file "$ROOT/experiments/topics-sample.txt" "$@"
