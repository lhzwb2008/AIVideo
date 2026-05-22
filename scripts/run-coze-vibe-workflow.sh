#!/usr/bin/env bash
# 调用「扣子编程」已部署工作流（*.coze.site/run），下载 output 视频
# 配置见 .env：COZE_VIBE_RUN_URL、COZE_VIBE_API_TOKEN
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "缺少 .env"
  exit 1
fi
# shellcheck disable=SC1091
source .env

: "${COZE_VIBE_RUN_URL:?请在 .env 设置 COZE_VIBE_RUN_URL（部署页 Curl 里的 /run 地址）}"
: "${COZE_VIBE_API_TOKEN:?请在 .env 设置 COZE_VIBE_API_TOKEN（部署页 API Token）}"

INPUT_KEY="${COZE_VIBE_INPUT_KEY:-input}"
TOPIC="${1:-${COZE_WORKFLOW_TOPIC:-今日AI新闻}}"

echo "[1/2] 执行工作流 (${INPUT_KEY}=${TOPIC}) …"
echo "  URL: ${COZE_VIBE_RUN_URL}"

BODY="$(INPUT_KEY="$INPUT_KEY" TOPIC="$TOPIC" python3 -c 'import json,os; k=os.environ["INPUT_KEY"]; print(json.dumps({k: os.environ["TOPIC"]}))')"

RUN_JSON="$(curl -sS -m 900 "${COZE_VIBE_RUN_URL}" \
  -H "Authorization: Bearer ${COZE_VIBE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "$BODY")"

python3 - "$RUN_JSON" <<'PY'
import json, sys, urllib.request, os
from datetime import datetime

raw = sys.argv[1]
r = json.loads(raw)

# 扣子编程部署 API：{"output":"https://...mp4","run_id":"..."}
url = r.get("output") or ""
if not url and "data" in r:
    data = r["data"]
    if isinstance(data, str):
        data = json.loads(data)
    url = (data or {}).get("output") or ""

if not url:
    detail = r.get("detail") or {}
    if isinstance(detail, dict) and detail.get("error_message"):
        print("工作流执行失败:", detail.get("error_message"), file=sys.stderr)
        if "cannot open resource" in str(detail.get("stack_trace", "")):
            print(
                "\n原因: 云端找不到字体文件（text_overlay_node.py 的 FONT_PATH）。\n"
                "修复: 在 code.coze.cn 打开项目，粘贴 docs/coze-vibe-fix-font.md 里的提示词，\n"
                "      修复后重新「部署」，再执行本脚本。\n"
                "修复后请在 code.coze.cn 重新「部署」再试。",
                file=sys.stderr,
            )
        else:
            print("堆栈:", "\n".join(detail.get("stack_trace") or [])[:2000], file=sys.stderr)
    else:
        print("未找到 output 视频 URL，完整响应:", json.dumps(r, ensure_ascii=False, indent=2), file=sys.stderr)
    sys.exit(1)

os.makedirs("output", exist_ok=True)
fname = os.path.join("output", datetime.now().strftime("%Y%m%d_%H%M%S") + ".mp4")
print("  run_id:", r.get("run_id", ""))
urllib.request.urlretrieve(url, fname)
log_dir = os.path.join(os.path.dirname(fname), "..", "logs")
os.makedirs(log_dir, exist_ok=True)
with open(os.path.join(log_dir, "last_vibe_run.json"), "w", encoding="utf-8") as f:
    f.write(json.dumps(r, ensure_ascii=False, indent=2))

print("[2/2] 已保存:", fname, f"({os.path.getsize(fname)} bytes)")
PY
