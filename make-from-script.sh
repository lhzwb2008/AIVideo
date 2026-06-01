#!/usr/bin/env bash
# AI财知道·直接喂文案模式：拿现成分页文案 → 生图 → 合成 → 抖音 → 归档 → YouTube / 小红书等
#
# 跳过 make-topics 的「搜文章 → 深读 → 改编」链路，适合你（或模型）已按生图要求
# 写好分页文案的特殊话题/场景。文案是一个 JSON 文件，结构见 src/make_from_script.py 顶部说明。
#
# 用法：
#   ./make-from-script.sh script.json                 # 制作并发布（推荐）
#   ./make-from-script.sh script.json --no-publish    # 只生成不发布
#   ./make-from-script.sh script.json --dry-run       # 预演发布参数，不真正发布
#   cat script.json | ./make-from-script.sh -         # 从 stdin 读文案
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] && set -a && source .env && set +a
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

# 可在参数里追加 --no-publish / --dry-run
python3 "$ROOT/src/make_from_script.py" "$@"
