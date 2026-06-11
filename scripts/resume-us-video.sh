#!/usr/bin/env bash
# 续发 US 视频：指定平台发布 + 归档（跳过生图/合成；默认只补 LinkedIn）
#
#   ./scripts/resume-us-video.sh output/en/20260611_111227.mp4 \
#     logs/en/last_script_20260611_110843_us02.json
#   ./scripts/resume-us-video.sh VIDEO SCRIPT linkedin
#   ./scripts/resume-us-video.sh VIDEO SCRIPT all   # IG + FB + LinkedIn
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export ROOT
source "$ROOT/scripts/load-dotenv.sh" en 2>/dev/null || true
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

VIDEO="${1:?用法: resume-us-video.sh VIDEO SCRIPT [platform|all]}"
SCRIPT="${2:?用法: resume-us-video.sh VIDEO SCRIPT [platform|all]}"
PLAT="${3:-linkedin}"

if [[ ! -f "$VIDEO" ]]; then
  echo "✗ 视频不存在: $VIDEO" >&2
  exit 1
fi
if [[ ! -f "$SCRIPT" ]]; then
  echo "✗ 脚本不存在: $SCRIPT" >&2
  exit 1
fi

echo "=== 续发 US 视频 ==="
echo "视频: $VIDEO"
echo "脚本: $SCRIPT"
echo "平台: $PLAT"
echo

"$ROOT/scripts/test-us-social.sh" publish "$PLAT" --headless --video "$VIDEO" --script "$SCRIPT"

export AIVIDEO_LOCALE=en
python3 - <<PY
import json
import sys
from datetime import datetime
from pathlib import Path

from paths import ROOT
from publish_pipeline import archive_publish_bundle, log, rel
from research import load_env
from us_market import append_us_history

load_env()
video = Path("$VIDEO")
if not video.is_absolute():
    video = ROOT / video
script_path = Path("$SCRIPT")
if not script_path.is_absolute():
    script_path = ROOT / script_path

data = json.loads(script_path.read_text(encoding="utf-8"))
script = data.get("script", data)
topic = data.get("topic") or {}

archived = archive_publish_bundle(video, date_tag=datetime.now().strftime("%Y%m%d"))
log(f"已归档：{rel(archived['video'])}")

append_us_history(
    {
        **script,
        "slot": topic.get("slot", script.get("slot", "")),
        "theme_cluster": topic.get("theme_cluster", script.get("theme_cluster", "")),
    },
    video=str(rel(archived["video"])),
)
log("已写入 us_market_history.json")
PY

echo
echo "✓ 续发完成"
