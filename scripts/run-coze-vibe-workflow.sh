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

mkdir -p logs
printf '%s' "$RUN_JSON" > logs/last_vibe_raw.json
echo "  原始响应已写入: logs/last_vibe_raw.json"

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
    print(json.dumps(r, ensure_ascii=False, indent=2), file=sys.stderr)
    detail = r.get("detail") or {}
    if isinstance(detail, dict) and detail.get("error_message"):
        trace = str(detail.get("stack_trace", ""))
        if "Read-only file system" in trace or "bytefaas" in trace:
            print(
                "\n原因: 部署环境只读，代码试图在 assets/ 下创建字体（font_dir.mkdir）。\n"
                "修复: 打开 docs/coze-vibe-fix-font.md，粘贴【提示词 B】到 code.coze.cn，\n"
                "      把 NotoSansSC-Regular.otf 放进 assets/fonts/ 后重新「部署」。",
                file=sys.stderr,
            )
        elif "ffmpeg" in trace or "FileNotFoundError" in str(detail.get("error_message", "")):
            print(
                "\n原因: 部署环境未安装 ffmpeg（video_clip_node 调 subprocess）。\n"
                "修复: docs/coze-vibe-fix-ffmpeg.md 粘贴提示词到 code.coze.cn → 重新部署。",
                file=sys.stderr,
            )
        elif "cannot open resource" in trace:
            print(
                "\n原因: 云端找不到字体文件。\n"
                "修复: docs/coze-vibe-fix-font.md →【提示词 A 或 B】→ 重新部署。",
                file=sys.stderr,
            )
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
