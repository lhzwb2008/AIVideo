#!/usr/bin/env bash
# 本地校验 Coze SAT 与百炼兼容接口（从项目根目录执行）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "缺少 .env：请先执行 cp .env.example .env 并填入密钥"
  exit 1
fi

# shellcheck disable=SC1091
source .env

fail=0

echo "== 百炼兼容接口 (chat/completions) =="
if [[ -z "${DASHSCOPE_API_KEY:-}" ]]; then
  echo "跳过：未设置 DASHSCOPE_API_KEY"
  fail=1
else
  resp="$(curl -sS -m 30 "${DASHSCOPE_BASE_URL}/chat/completions" \
    -H "Authorization: Bearer ${DASHSCOPE_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${DASHSCOPE_MODEL:-qwen-plus}\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":8}")"
  if echo "$resp" | grep -q '"choices"'; then
    echo "OK"
  else
    echo "失败: $resp"
    fail=1
  fi
fi

echo ""
echo "== Coze SAT =="
if [[ -z "${COZE_API_TOKEN:-}" ]]; then
  echo "跳过：未设置 COZE_API_TOKEN"
  fail=1
else
  # 未配置 workflow_id 时只测令牌是否被接受（会返回业务错误而非 401）
  body='{}'
  if [[ -n "${COZE_WORKFLOW_ID:-}" ]]; then
    body=$(printf '{"workflow_id":"%s","parameters":{}}' "$COZE_WORKFLOW_ID")
  else
    body='{"workflow_id":"0","parameters":{}}'
    echo "提示: 未设置 COZE_WORKFLOW_ID，将收到 workflow 相关错误属正常；401/4101 才表示令牌无效"
  fi
  resp="$(curl -sS -m 30 "${COZE_API_BASE}/v1/workflow/run" \
    -H "Authorization: Bearer ${COZE_API_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$body")"
  if echo "$resp" | grep -qE '"code"[[:space:]]*:[[:space:]]*0'; then
    echo "工作流调用 OK"
  elif echo "$resp" | grep -qE '401|4101|unauthorized|Unauthorized'; then
    echo "失败（鉴权）: $resp"
    fail=1
  else
    echo "令牌已到达 API（业务响应）: $resp"
  fi
fi

exit "$fail"
