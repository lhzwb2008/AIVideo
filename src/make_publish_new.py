#!/usr/bin/env python3
"""新流水线：Cursor Cloud Agent 五槽位调研写稿 → Opus 深读+改编 → 生图合成发布。

不再使用 Exa 搜文选题；每日固定顺序 5 条：
  A股大盘 → A股热点板块 → 国内财经 → AI 热点 → 世界财经
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

import cost_tracker
from batch_aivideo import append_history_from_script
from cursor_daily_topics import (
    CURSOR_SLOT_ORDER,
    SLOT_LABEL,
    build_cursor_topic_research,
    discover_cursor_topics,
)
from paths import ROOT
from publish_pipeline import log, process_topic
from locale_env import load_locale_env, locale_logs_dir
from research import load_env


def main() -> int:
    load_locale_env("zh")
    os.environ.setdefault("AIVIDEO_SOURCE", "cursor")
    os.environ.setdefault("AIVIDEO_COMPLIANCE_RELAXED", "1")
    default_count = len(CURSOR_SLOT_ORDER)
    parser = argparse.ArgumentParser(
        description="AI财知道（Cursor 调研版）：五槽位固定顺序 → Opus 改编 → 发布"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=int(os.environ.get("AIVIDEO_MAX_VIDEOS_PER_RUN", str(default_count))),
        help=f"本次制作条数（默认 {default_count}，最大建议 {default_count}）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只预演发布参数")
    parser.add_argument("--no-publish", action="store_true", help="只生成视频，跳过发布")
    parser.add_argument(
        "--draft-only",
        action="store_true",
        help="只跑 Cursor 写稿+Opus 深读，不生成视频（调试用）",
    )
    args = parser.parse_args()

    target = max(1, min(args.count, len(CURSOR_SLOT_ORDER)))
    topics = discover_cursor_topics(target=target)
    if not topics:
        log("没有可用槽位。")
        return 0

    log(
        f"【Cursor 调研版】本次 {target} 条；槽位顺序："
        + " → ".join(SLOT_LABEL[t["slot"]] for t in topics)
    )
    log("调研：Cursor Cloud Agent 联网写稿 | 改编：Opus 深读+短视频脚本")

    run_start = time.time()
    made: list[dict] = []
    failed: list[dict] = []
    agent_id: str | None = None
    reuse = os.environ.get("AIVIDEO_CURSOR_REUSE_AGENT", "1").strip() not in ("0", "false", "no")

    for index, topic in enumerate(topics, 1):
        slot = topic["slot"]
        title_hint = topic["title_hint"]
        log(f"\n>>> 槽位 #{index}/{target} [{SLOT_LABEL[slot]}]：{title_hint}")
        try:
            article, details, agent_id = build_cursor_topic_research(
                topic,
                agent_id=agent_id if reuse else None,
            )
            if not reuse:
                agent_id = None

            if args.draft_only:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                out = locale_logs_dir("zh") / f"cursor_research_{stamp}_topic{index:02d}.json"
                out.write_text(
                    json.dumps(
                        {"topic": topic, "article": article, "details": details},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                log(f"  ✓ draft-only 已保存 {out}")
                made.append({"slot": slot, "title_hint": title_hint, "draft": str(out)})
                continue

            result = process_topic(
                index,
                target=target,
                topic=topic,
                article=article,
                details=details,
                publish_check=False,
                dry_run=args.dry_run,
                skip_publish=args.no_publish,
                append_history_fn=append_history_from_script,
            )
            result["slot"] = slot
            result["cursor_slot"] = slot
            result["topic_slot"] = slot
            result["direction"] = slot
            result["title_hint"] = title_hint
            made.append(result)
        except Exception as exc:  # noqa: BLE001
            log(f"\n✗ 槽位失败 [{SLOT_LABEL.get(slot, slot)}]：{exc}")
            failed.append({"slot": slot, "title": title_hint, "error": str(exc)})
            if reuse:
                agent_id = None

    summary = locale_logs_dir("zh") / "make_publish_new_last.json"
    summary.write_text(
        json.dumps(
            {
                "mode": "cursor_daily_slots",
                "target": target,
                "slots": [t["slot"] for t in topics],
                "made": made,
                "failed": failed,
                "agent_id": agent_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    log(f"\n全部完成：成功 {len(made)}/{target}")
    for item in made:
        tag = SLOT_LABEL.get(item.get("slot", ""), item.get("slot"))
        title = item.get("title") or item.get("title_hint")
        video = item.get("video") or item.get("draft") or "-"
        log(f"  ✓ [{tag}] {title} → {video}")
    if failed:
        log(f"\n失败 {len(failed)} 条：")
        for item in failed:
            log(f"  ✗ {SLOT_LABEL.get(item.get('slot'), '?')} → {item.get('error')}")
    if not args.draft_only:
        log("\n" + cost_tracker.report_window(run_start, videos=len([m for m in made if m.get("video")])))
    return 0 if len(made) >= target and not failed else (1 if failed else 0)


if __name__ == "__main__":
    raise SystemExit(main())
