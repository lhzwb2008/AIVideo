#!/usr/bin/env bash
# AI财知道·指定话题模式：把一段含编号的话拆成多个话题 → 逐个生成视频 → 发布抖音 → 归档
#
# 用法：
#   ./make-topics.sh                                   # 直接回车，按提示在命令行输入话题
#   ./make-topics.sh "1 小鹏财报 2 韬定律是什么 3 opus4.8发布"
#   ./make-topics.sh --no-publish "1 小鹏财报 2 韬定律是什么"   # 只生成不发布
#   ./make-topics.sh --file topics.txt
#   echo "1 小鹏财报 2 韬定律是什么" | ./make-topics.sh -
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && set -a && source .env && set +a
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

# 默认发布前检查抖音登录态；可在参数里追加 --no-publish / --dry-run
python3 "$ROOT/src/make_topics_publish.py" --check "$@"
