#!/usr/bin/env bash
# 全自动：调用 Coze 工作流 aivideo → 下载 MP4 到 output/
# 用法: ./scripts/run-coze-workflow.sh [可选：input 主题，默认读 COZE_WORKFLOW_TOPIC]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "缺少 .env，请 cp .env.example .env 并配置密钥"
  exit 1
fi
# shellcheck disable=SC1091
source .env

: "${COZE_API_TOKEN:?}"
: "${COZE_WORKFLOW_ID:?}"
COZE_API_BASE="${COZE_API_BASE:-https://api.coze.cn}"
TOPIC="${1:-${COZE_WORKFLOW_TOPIC:-今日AI新闻}}"

echo "[1/3] 查询工作流 …"
META="$(curl -sS -m 30 "${COZE_API_BASE}/v1/workflows/${COZE_WORKFLOW_ID}?include_input_output=true" \
  -H "Authorization: Bearer ${COZE_API_TOKEN}")"
echo "$META" | python3 -c "import json,sys; r=json.load(sys.stdin); assert r['code']==0, r; print('  名称:', r['data']['workflow_detail']['workflow_name'])" || {
  echo "$META"
  exit 1
}

echo "[2/3] 执行工作流 (input=${TOPIC}) …"
BODY="$(TOPIC="$TOPIC" WID="$COZE_WORKFLOW_ID" python3 -c 'import json,os; print(json.dumps({"workflow_id":os.environ["WID"],"parameters":{"input":os.environ["TOPIC"]}}))')"
RUN_JSON="$(curl -sS -m 600 "${COZE_API_BASE}/v1/workflow/run" \
  -H "Authorization: Bearer ${COZE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$BODY")"

python3 - "$RUN_JSON" <<'PY'
import json, sys, urllib.request, os
from datetime import datetime

r = json.loads(sys.argv[1])
if r.get("code") != 0:
    print("失败:", r.get("msg"), r, file=sys.stderr)
    sys.exit(1)

data = json.loads(r["data"])
url = data.get("output") or ""
if not url:
    print("无 output 视频字段:", r["data"], file=sys.stderr)
    sys.exit(1)

os.makedirs("output", exist_ok=True)
fname = os.path.join("output", datetime.now().strftime("%Y%m%d_%H%M%S") + ".mp4")
print("  debug:", r.get("debug_url", ""))
urllib.request.urlretrieve(url, fname)
with open("output/last_run.json", "w", encoding="utf-8") as f:
    f.write(json.dumps(r, ensure_ascii=False, indent=2))

print("[3/3] 已保存:", fname, f"({os.path.getsize(fname)} bytes)")
PY
