#!/usr/bin/env bash
# AI财知道·指定话题模式：逐个生成视频 → 抖音 → 归档 → YouTube / 小红书等
#
# 默认直接读取项目根目录的 topics.txt（每行一个话题，行首可写栏目名，如「基础 如何给企业估值」）
#
# 用法：
#   ./make-topics.sh                                   # 读 topics.txt 制作并发布（推荐）
#   ./make-topics.sh --no-publish                      # 读 topics.txt 只生成不发布
#   ./make-topics.sh --file other.txt                  # 指定别的清单文件
#   ./make-topics.sh "1 小鹏财报 2 韬定律是什么"         # 仍兼容把话题直接作参数传
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && set -a && source .env && set +a
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

# 默认发布前检查抖音登录态；可追加 --no-publish / --dry-run
python3 "$ROOT/src/make_topics_publish.py" --check "$@"
