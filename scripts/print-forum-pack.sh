#!/usr/bin/env bash
# 生成论坛发帖包：与视频同目录的同名文件夹（output/xxx/ 或指定路径）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

SCRIPT="${1:?用法: print-forum-pack.sh <script.json> <video.mp4>}"
VIDEO="${2:?用法: print-forum-pack.sh <script.json> <video.mp4>}"

[[ -f "$SCRIPT" ]] || { echo "脚本不存在: $SCRIPT" >&2; exit 1; }
[[ -f "$VIDEO" ]] || { echo "视频不存在: $VIDEO" >&2; exit 1; }

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -c "
from pathlib import Path
from forum_manual_pack import build_forum_pack, forum_dir_for_video

video = Path('$VIDEO')
info = build_forum_pack(Path('$SCRIPT'), video)
print('目录:', info['out_dir'])
print('正文:', info['post_md'])
print('竖封面:', info['cover'] or '(未生成)')
print('横封面:', info.get('cover_landscape') or '(未生成)')
print('配图:', len(info['images']), '张')
"

DIR="$(cd "$(dirname "$VIDEO")" && pwd)/$(basename "$VIDEO" .mp4)"
open "$DIR/post.md" 2>/dev/null || true
