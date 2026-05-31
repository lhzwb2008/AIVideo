#!/usr/bin/env python3
"""一键制作并发布：每日问句话题选题 → 搜文深读 → 改编 → 生图 → 合成 → 发布。"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

import cost_tracker
import specified_topics
from batch_aivideo import append_history_from_script
from daily_topics import DIRECTION_LABEL, discover_daily_topics
from paths import ROOT
from publish_pipeline import log, process_topic
from research import load_env


def main() -> int:
    load_env()
    os.environ["AIVIDEO_SOURCE"] = "exa"
    parser = argparse.ArgumentParser(description="AI财知道：每日话题模式制作并自动发布")
    parser.add_argument(
        "--count",
        type=int,
        default=int(os.environ.get("AIVIDEO_MAX_VIDEOS_PER_RUN", "3")),
        help="本次成功制作并发布的视频数（默认 3）",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=int(os.environ.get("AIVIDEO_DAYS", os.environ.get("DAILY_RUN_DAYS", "7"))),
        help="发现热点候选的时间窗（天）；单条搜文用 AIVIDEO_TOPIC_DAYS（默认 7）",
    )
    parser.add_argument("--check", action="store_true", help="发布前检查抖音登录态")
    parser.add_argument("--dry-run", action="store_true", help="只预演发布参数，不真正发布/归档")
    parser.add_argument("--no-publish", action="store_true", help="只生成视频，跳过发布")
    args = parser.parse_args()

    target = max(1, args.count)
    topic_days = int(os.environ.get("AIVIDEO_TOPIC_DAYS", "7"))

    topics = discover_daily_topics(days=args.days, target=target)
    if not topics:
        log("没有可用话题，本次不制作。")
        return 0

    run_start = time.time()
    made: list[dict] = []
    failed: list[dict] = []

    for index, topic in enumerate(topics, 1):
        title_hint = topic["title_hint"]
        log(f"\n>>> 话题 #{index}/{target}：{title_hint}")
        try:
            article, details = specified_topics.build_topic_research(topic, days=topic_days)
            result = process_topic(
                index,
                target=target,
                topic=topic,
                article=article,
                details=details,
                publish_check=args.check,
                dry_run=args.dry_run,
                skip_publish=args.no_publish,
                append_history_fn=append_history_from_script,
            )
            result["direction"] = topic.get("direction")
            result["title_hint"] = title_hint
            made.append(result)
        except Exception as exc:  # noqa: BLE001
            log(f"\n✗ 话题失败：{title_hint}：{exc}")
            failed.append({"title": title_hint, "error": str(exc)})

    summary = ROOT / "logs" / "make_publish_last.json"
    summary.write_text(
        json.dumps(
            {
                "mode": "daily_topics",
                "target": target,
                "discover_days": args.days,
                "topic_search_days": topic_days,
                "topics": topics,
                "made": made,
                "failed": failed,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    made_by_dir: dict[str, int] = {}
    for item in made:
        d = str(item.get("direction") or "ai")
        made_by_dir[d] = made_by_dir.get(d, 0) + 1
    cover_brief = "，".join(
        f"{DIRECTION_LABEL.get(d, d)} {made_by_dir[d]}" for d in ("astock", "ai", "hkus") if made_by_dir.get(d)
    )
    log(f"\n全部完成：成功 {len(made)}/{target}（{cover_brief or '无'}）")
    for item in made:
        tag = DIRECTION_LABEL.get(item.get("direction", ""), "?")
        log(f"  ✓ [{tag}] {item.get('title')} → {item.get('video')}")
    if failed:
        log(f"\n失败 {len(failed)} 条：")
        for item in failed:
            log(f"  ✗ {item.get('title')} → {item.get('error')}")
    log("\n" + cost_tracker.report_window(run_start, videos=len(made)))
    return 0 if len(made) >= target else 1


if __name__ == "__main__":
    raise SystemExit(main())
