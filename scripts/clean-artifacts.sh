#!/usr/bin/env bash
# 清理过程产物，仅保留 output/*.mp4 最终成片
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

rm -rf output/slideshow logs tmp .temp 2>/dev/null || true
find output -maxdepth 1 \( -name '*.jpg' -o -name '*.png' -o -name '*.json' -o -name '*.mp3' \) -delete 2>/dev/null || true
find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "已清理过程文件。保留成片:"
ls -lh output/*.mp4 2>/dev/null || echo "  (output 中暂无 mp4)"
