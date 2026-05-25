#!/usr/bin/env bash
# Coze 合成：把本地 JSON 脚本发给已部署工作流，下载 MP4
# 用法: ./run-coze.sh [logs/last_script.json]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

[[ -f .env ]] || { echo "缺少 .env"; exit 1; }
# shellcheck disable=SC1091
set -a
source .env
set +a

export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

: "${COZE_VIBE_RUN_URL:?请设置 COZE_VIBE_RUN_URL}"
: "${COZE_VIBE_API_TOKEN:?请设置 COZE_VIBE_API_TOKEN}"

INPUT_KEY="${COZE_VIBE_INPUT_KEY:-input}"
SCRIPT_FILE="${1:-logs/last_script.json}"
USE_STREAM="${COZE_VIBE_USE_STREAM:-1}"
MAX_ATTEMPTS="${COZE_VIBE_MAX_ATTEMPTS:-2}"

if [[ ! -f "$SCRIPT_FILE" ]]; then
  echo "找不到: $SCRIPT_FILE"
  echo "请先: ./run-aivideo.sh 或 python3 src/research.py \"今日AI新闻\"（需在项目根目录且 PYTHONPATH=src）"
  echo "或一键: ./run-aivideo.sh"
  exit 1
fi

echo "[1/2] Coze 合成 …"
echo "  脚本: $SCRIPT_FILE"
echo "  URL:  $COZE_VIBE_RUN_URL"

mkdir -p logs
RUN_JSON=""
for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  STREAM_FLAG="--use-stream"
  [[ "$USE_STREAM" == "0" ]] && STREAM_FLAG="--no-use-stream"

  ERR_LOG="logs/last_vibe_err.txt"
  set +e
  RUN_JSON="$(python3 "$ROOT/src/coze_client.py" "$SCRIPT_FILE" \
    --input-key "$INPUT_KEY" \
    --run-url "$COZE_VIBE_RUN_URL" \
    --token "$COZE_VIBE_API_TOKEN" \
    $STREAM_FLAG \
    -o logs/last_vibe_raw.json 2>"$ERR_LOG")"
  STATUS=$?
  set -e
  [[ -s "$ERR_LOG" ]] && cat "$ERR_LOG" >&2

  if [[ "$STATUS" -eq 0 ]]; then
    break
  fi

  if [[ "$attempt" -lt "$MAX_ATTEMPTS" ]]; then
    echo "  ⚠️  第 ${attempt} 次失败，15 秒后重试…" >&2
    sleep 15
  else
    [[ -s "$ERR_LOG" ]] && cat "$ERR_LOG" >&2
    echo "Coze 调用失败" >&2
    echo "原始响应已写入: logs/last_vibe_raw.json" >&2
    exit 1
  fi
done

python3 - "$RUN_JSON" "$SCRIPT_FILE" <<'PY'
import json, sys, urllib.request, os
from datetime import datetime, timezone

script_file = sys.argv[2] if len(sys.argv) > 2 else "logs/last_script.json"
raw = sys.argv[1].strip()
if not raw:
    print("Coze 返回空响应", file=sys.stderr)
    sys.exit(1)
try:
    r = json.loads(raw)
except json.JSONDecodeError:
    print(f"Coze 返回非 JSON: {raw[:300]}", file=sys.stderr)
    sys.exit(1)

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
        print("\n错误:", detail.get("error_message"), file=sys.stderr)
    sys.exit(1)

os.makedirs("output", exist_ok=True)
fname = os.path.join("output", datetime.now().strftime("%Y%m%d_%H%M%S") + ".mp4")
print("  run_id:", r.get("run_id", ""))
urllib.request.urlretrieve(url, fname)
os.makedirs("logs", exist_ok=True)
with open("logs/last_vibe_run.json", "w", encoding="utf-8") as f:
    f.write(json.dumps(r, ensure_ascii=False, indent=2))
with open("logs/last_video.txt", "w", encoding="utf-8") as f:
    f.write(fname + "\n")
manifest = os.path.join("logs", "video_manifest.jsonl")
with open(manifest, "a", encoding="utf-8") as mf:
    mf.write(json.dumps({
        "video": fname,
        "script": script_file,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False) + "\n")
print("[2/2] 已保存:", fname, f"({os.path.getsize(fname)} bytes)")
PY
