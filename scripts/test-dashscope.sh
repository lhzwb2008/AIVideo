#!/usr/bin/env bash
# 百炼 API 连通性诊断（不打印完整 Key）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
[[ -f .env ]] && source .env

KEY="${DASHSCOPE_API_KEY:-}"
BASE="${DASHSCOPE_BASE_URL:-https://dashscope.aliyuncs.com/compatible-mode/v1}"
MODEL="${DASHSCOPE_MODEL:-qwen-plus}"

if [[ -z "$KEY" ]]; then echo "未设置 DASHSCOPE_API_KEY"; exit 1; fi

echo "Key: sk-${KEY:3:6}... (len=${#KEY})"
echo "出口 IP: $(curl -sS -m 8 https://ifconfig.me 2>/dev/null || echo unknown)"
echo ""

chat() {
  curl -sS -m 20 "${BASE}/chat/completions" \
    -H "Authorization: Bearer ${KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"回复：通\"}],\"max_tokens\":8}"
}

echo "=== 兼容模式 ${BASE} ==="
R=$(chat)
if echo "$R" | grep -q '"choices"'; then
  echo "OK:" "$(echo "$R" | python3 -c "import json,sys; print(json.load(sys.stdin)['choices'][0]['message']['content'])")"
  exit 0
fi
echo "$R" | python3 -m json.tool 2>/dev/null || echo "$R"
echo ""
echo "若为 IP access denied：到 百炼控制台 → API-KEY 管理 → 该 Key → 检查「IP 白名单」是否包含上面出口 IP"
exit 1
