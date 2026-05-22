#!/usr/bin/env bash
# 百炼 OpenAI 兼容接口 · 对话（备用工具）
# 用法: ./scripts/bailian-chat.sh "你的问题"
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && source .env
: "${DASHSCOPE_API_KEY:?}"
BASE="${DASHSCOPE_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
MODEL="${DASHSCOPE_MODEL:-qwen-plus}"
PROMPT="${1:-你好}"

BODY="$(MODEL="$MODEL" PROMPT="$PROMPT" python3 -c 'import json,os; print(json.dumps({"model":os.environ["MODEL"],"messages":[{"role":"user","content":os.environ["PROMPT"]}],"max_tokens":2048}))')"

curl -sS -m 120 "${BASE}/chat/completions" \
  -H "Authorization: Bearer ${DASHSCOPE_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "$BODY" \
  | python3 -c "import json,sys; r=json.load(sys.stdin); print(r.get('choices',[{}])[0].get('message',{}).get('content',r))"
