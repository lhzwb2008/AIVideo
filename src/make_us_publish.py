#!/usr/bin/env python3
"""US Market 英文热点：一次生成 1 条视频，仅发布 YouTube + TikTok。"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

import cost_tracker
from paths import ROOT
from publish_pipeline import log, pipeline_after_script
from research import load_env
from us_market import append_us_history, build_us_research, discover_us_topic, run_us_research
from us_voice import apply_voice_env, list_voices, resolve_voice


def apply_us_publish_env(*, voice_id: str | None = None) -> str:
    """设置英文流水线默认环境：仅 YT/TikTok，关闭论坛与国内平台。"""
    os.environ["AIVIDEO_LOCALE"] = "en"
    os.environ["AIVIDEO_SOURCE"] = "exa"
    os.environ["AIVIDEO_FORUM_POST"] = "0"
    os.environ["AIVIDEO_PUBLISH_YOUTUBE"] = "1"
    os.environ.setdefault("AIVIDEO_PUBLISH_TIKTOK", "1")
    for key in (
        "AIVIDEO_PUBLISH_BILIBILI",
        "AIVIDEO_PUBLISH_DOUYIN",
        "AIVIDEO_PUBLISH_EASTMONEY",
        "AIVIDEO_PUBLISH_XUEQIU",
        "AIVIDEO_PUBLISH_WECHAT",
        "AIVIDEO_PUBLISH_ZHIHU",
        "AIVIDEO_PUBLISH_XHS",
        "AIVIDEO_PUBLISH_KS",
        "AIVIDEO_PUBLISH_SHIPINHAO",
    ):
        os.environ[key] = "0"
    os.environ.setdefault("AIVIDEO_BRAND_NAME", "Market Sketch")
    os.environ.setdefault("AIVIDEO_BRAND_TAGLINE", "US markets in plain English")
    os.environ.setdefault(
        "AIVIDEO_OUTRO_NARRATION",
        "That's the sketch for today. Save it if useful — and tell me what you'd watch next.",
    )
    os.environ.setdefault("YOUTUBE_HASHTAGS", "#stocks #USmarket #finance #investing #Shorts")
    os.environ.setdefault("TIKTOK_HASHTAGS", "#stocks #finance #investing #wallstreet #money")
    os.environ.setdefault("YOUTUBE_DISCLAIMER", "For education only. Not investment advice.")
    os.environ.setdefault("TIKTOK_DISCLAIMER", "For education only. Not investment advice.")
    return apply_voice_env(voice_id)


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="US Market：英文热点分析 → 生图 → 合成 → YouTube/TikTok")
    parser.add_argument("--days", type=int, default=int(os.environ.get("US_MARKET_DAYS", os.environ.get("AIVIDEO_DAYS", "3"))), help="热点搜索时间窗（天）")
    parser.add_argument("--topic", help="手动指定英文话题（跳过自动选题）")
    parser.add_argument("--voice", help="音色 ID，见 assets/us-voices.json 或 --list-voices")
    parser.add_argument("--list-voices", action="store_true", help="列出可选英文男声")
    parser.add_argument("--dry-run", action="store_true", help="预演发布参数")
    parser.add_argument("--no-publish", action="store_true", help="只生成视频")
    args = parser.parse_args()

    if args.list_voices:
        for row in list_voices():
            print(f"  {row['id']:10}  {row.get('name', '')} — {row.get('desc', '')}", flush=True)
        print("\n试听: ./scripts/preview-us-voices.sh", flush=True)
        print("使用: US_TTS_VOICE=longshu ./make-us-publish.sh", flush=True)
        return 0

    voice = apply_us_publish_env(voice_id=args.voice)
    _, voice_cfg = resolve_voice(voice)
    log(f"US Market 模式 | locale=en | voice={voice} ({voice_cfg.get('name', '')})")
    log("发布: YouTube + TikTok only")

    run_start = time.time()
    if args.topic:
        topic = {
            "direction": "usmarket",
            "category": "usmarket",
            "title_hint": args.topic.strip(),
            "cold_open": "",
            "theme_cluster": "manual",
            "angle": "user provided topic",
        }
        log(f"手动话题: {topic['title_hint']}")
    else:
        log("正在发现 US market 热点话题…")
        topic = discover_us_topic(days=args.days)
        log(f"选题: {topic.get('title_hint')}")
        if topic.get("cold_open"):
            log(f"  cold_open: {topic['cold_open']}")

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    script_path = ROOT / "logs" / f"last_script_{stamp}_us01.json"
    article, details = build_us_research(topic, days=args.days)
    script = run_us_research(
        output=script_path,
        topic=topic,
        article=article,
        details=details,
        days=args.days,
    )
    title = str(script.get("title") or "").strip()
    log(f"脚本标题: {title}")

    def _append(_path):  # noqa: ANN001
        append_us_history(script)

    result = pipeline_after_script(
        script_path,
        title,
        index=1,
        target=1,
        publish_check=False,
        dry_run=args.dry_run,
        skip_publish=args.no_publish,
        append_history_fn=_append,
    )
    append_us_history(script, video=str(result.get("video") or ""))

    summary = ROOT / "logs" / "make_us_publish_last.json"
    summary.write_text(
        json.dumps(
            {
                "mode": "us_market",
                "locale": "en",
                "voice": voice,
                "topic": topic,
                "result": result,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log(f"\n完成 → {result.get('video')}")
    if result.get("youtube_url"):
        log(f"  YouTube: {result['youtube_url']}")
    if result.get("tiktok_url"):
        log(f"  TikTok: {result['tiktok_url']}")
    log("\n" + cost_tracker.report_window(run_start, videos=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
