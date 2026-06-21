#!/usr/bin/env python3
"""中文流水线：工作日五槽位新闻 / 周末三槽位科普 → Opus 深读+改编 → 生图合成发布。

工作日固定顺序 5 条：
  A股大盘 → A股热点板块 → 国内财经 → AI 热点 → 世界财经

周末固定顺序 3 条（科普教育，与新闻槽位分离）：
  财经基础 → 量化入门 → 估值与计算
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
    SLOT_TO_CATEGORY,
    build_cursor_topic_research,
    china_today,
    discover_cursor_topics,
    topic_plan_for_slot,
)
from paths import ROOT
from publish_pipeline import log, process_topic, recover_missing_forum_packs
from locale_env import load_locale_env, locale_logs_dir
from research import load_env
from weekend_edu_topics import (
    ALL_SLOT_CHOICES,
    EDU_SLOT_LABEL,
    build_weekend_edu_research,
    discover_weekend_edu_topics,
    is_weekend_edu_mode,
    topic_for_edu_slot,
    weekend_default_count,
)


def _default_count() -> int:
    return weekend_default_count() if is_weekend_edu_mode() else len(CURSOR_SLOT_ORDER)


def _slot_label(slot: str) -> str:
    if slot.startswith("edu_"):
        cat = slot.replace("edu_", "", 1)
        return EDU_SLOT_LABEL.get(cat, slot)
    return SLOT_LABEL.get(slot, slot)


def _topic_for_slot(slot: str) -> dict:
    """为 --slot 指定槽位构建话题。"""
    if slot.startswith("edu_"):
        return topic_for_edu_slot(slot)
    label = SLOT_LABEL[slot]
    today = china_today().isoformat()
    plan = topic_plan_for_slot(slot)
    row = {
        "index": 1,
        "slot": slot,
        "direction": slot,
        "cursor_slot": slot,
        "title_hint": plan.get("title_hint") or f"{today} {label}",
        "category": SLOT_TO_CATEGORY.get(slot, "ai"),
        "theme_cluster": plan.get("theme_cluster") or f"cursor_{slot}",
        "angle": plan.get("angle") or label,
        "reason": f"指定槽位重跑：{label}",
    }
    for key in ("suggested_video_title", "cold_open", "script_mode"):
        if plan.get(key):
            row[key] = plan[key]
    return row


def _discover_topics(*, target: int) -> list[dict]:
    if is_weekend_edu_mode():
        return discover_weekend_edu_topics(target=target)
    return discover_cursor_topics(target=target)


def _build_research(topic: dict, *, agent_id: str | None):
    if is_weekend_edu_mode() or topic.get("mode") == "weekend_edu":
        return build_weekend_edu_research(topic, agent_id=agent_id)
    return build_cursor_topic_research(topic, agent_id=agent_id)


def main() -> int:
    load_locale_env("zh")
    os.environ.setdefault("AIVIDEO_SOURCE", "cursor")
    os.environ.setdefault("AIVIDEO_COMPLIANCE_RELAXED", "1")
    weekend = is_weekend_edu_mode()
    default_count = _default_count()
    mode_desc = (
        "周末科普教育（基础/量化/估值）"
        if weekend
        else "工作日五槽位新闻"
    )
    parser = argparse.ArgumentParser(
        description=f"AI财知道：{mode_desc} → Opus 改编 → 发布"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=int(os.environ.get("AIVIDEO_MAX_VIDEOS_PER_RUN", str(default_count))),
        help=f"本次制作条数（默认 {default_count}）",
    )
    parser.add_argument(
        "--slot",
        choices=ALL_SLOT_CHOICES,
        help="只跑指定槽位（工作日如 astock_market；周末如 edu_basic）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只预演发布参数")
    parser.add_argument("--no-publish", action="store_true", help="只生成视频，跳过发布")
    parser.add_argument(
        "--draft-only",
        action="store_true",
        help="只跑写稿+Opus 深读，不生成视频（调试用）",
    )
    args = parser.parse_args()

    max_slots = 99 if weekend else len(CURSOR_SLOT_ORDER)

    if args.slot:
        target = 1
        topics = [_topic_for_slot(args.slot)]
        weekend = args.slot.startswith("edu_") or is_weekend_edu_mode()
    else:
        target = max(1, min(args.count, max_slots))
        topics = _discover_topics(target=target)
    if not topics:
        log("没有可用槽位/话题。")
        return 0

    pipeline_mode = "weekend_edu" if weekend else "cursor_daily_slots"
    log(f"模式：{mode_desc}")
    log(
        f"本次 {target} 条；顺序："
        + " → ".join(_slot_label(t["slot"]) for t in topics)
    )
    if weekend:
        log("调研：Opus 动态选题 + Cursor 科普写稿 | 改编：Opus 深读+短视频脚本")
    else:
        log("调研：Cursor Cloud Agent 联网写稿 | 改编：Opus 深读+短视频脚本")

    run_start = time.time()
    made: list[dict] = []
    failed: list[dict] = []
    agent_id: str | None = None
    reuse = os.environ.get("AIVIDEO_CURSOR_REUSE_AGENT", "1").strip() not in ("0", "false", "no")

    for index, topic in enumerate(topics, 1):
        slot = topic["slot"]
        title_hint = topic["title_hint"]
        log(f"\n>>> #{index}/{target} [{_slot_label(slot)}]：{title_hint}")
        try:
            article, details, agent_id = _build_research(
                topic,
                agent_id=agent_id if reuse else None,
            )
            if not reuse:
                agent_id = None

            if args.draft_only:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                prefix = "edu_research" if weekend else "cursor_research"
                out = locale_logs_dir("zh") / f"{prefix}_{stamp}_topic{index:02d}.json"
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
            if topic.get("topic_id"):
                result["topic_id"] = topic["topic_id"]
            made.append(result)
        except Exception as exc:  # noqa: BLE001
            log(f"\n✗ 失败 [{_slot_label(slot)}]：{exc}")
            failed.append({"slot": slot, "title": title_hint, "error": str(exc)})
            if reuse:
                agent_id = None

    summary = locale_logs_dir("zh") / "make_publish_last.json"
    summary.write_text(
        json.dumps(
            {
                "mode": pipeline_mode,
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
        tag = _slot_label(item.get("slot", ""))
        title = item.get("title") or item.get("title_hint")
        video = item.get("video") or item.get("draft") or "-"
        log(f"  ✓ [{tag}] {title} → {video}")
    if failed:
        log(f"\n失败 {len(failed)} 条：")
        for item in failed:
            log(f"  ✗ {_slot_label(item.get('slot', '?'))} → {item.get('error')}")
    if not args.draft_only:
        recover_missing_forum_packs(made)
        log("\n" + cost_tracker.report_window(run_start, videos=len([m for m in made if m.get("video")])))
    return 0 if len(made) >= target and not failed else (1 if failed else 0)


if __name__ == "__main__":
    raise SystemExit(main())
