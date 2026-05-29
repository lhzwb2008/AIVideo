#!/usr/bin/env python3
"""指定话题模式一键制作并发布：

把命令行里给的一段话拆成多个话题 → 每个话题搜文章/用自带内容/模型自写
→ 改编脚本 → 生图 → 合成 → 抖音发布 → 归档。

用法：
  python3 src/make_topics_publish.py "1 小鹏财报，2 韬定律是什么，3 opus4.8发布"
  python3 src/make_topics_publish.py --file topics.txt
  echo "1 小鹏财报，2 韬定律是什么" | python3 src/make_topics_publish.py -
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import time

import cost_tracker
import specified_topics
from make_publish import (
    archive_video,
    latest_video,
    log,
    read_script_title,
    rel,
    run,
)
from batch_aivideo import append_history_from_script
from paths import ROOT
from publish_all_douyin import load_published, save_published
from research import load_env, run_article_research


_PROMPT = (
    "\n请输入今天要做的话题（一行，用 1 2 3 编号分隔；不编号也行，会自动按你写的拆）：\n"
    "例如：1 小鹏财报 2 韬定律是什么 3 opus4.8发布\n> "
)


def read_topics_text(args: argparse.Namespace) -> str:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    parts = [p for p in (args.topics or []) if p]
    if parts == ["-"]:
        return sys.stdin.read()
    if parts:
        return " ".join(parts)
    # 没有任何参数：交互式从命令行读一行；非交互（管道）则读 stdin
    if not sys.stdin.isatty():
        return sys.stdin.read()
    try:
        return input(_PROMPT).strip()
    except EOFError:
        return ""


def process_topic(
    index: int,
    *,
    target: int,
    topic: dict,
    article: dict,
    details: dict,
    publish_check: bool,
    dry_run: bool,
    skip_publish: bool = False,
) -> dict:
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    script_path = logs_dir / f"last_script_{stamp}_topic{index:02d}.json"

    log(f"\n=== [{index}/{target}] 话题：{topic.get('title_hint')} ===")
    script, _ = run_article_research(
        output=script_path,
        auto_pick=True,
        source="exa",
        preselected_article=article,
        preselected_details=details,
    )
    title = str(script.get("title") or read_script_title(script_path) or "").strip()
    log(f"脚本标题：{title}")

    run([str(ROOT / "scripts" / "run-enrich-images.sh"), str(script_path)], label="生图")
    run([str(ROOT / "scripts" / "run-compose.sh"), str(script_path)], label="合成")
    video = latest_video()

    if skip_publish:
        log(f"\n=== [{index}/{target}] 跳过发布（--no-publish）===")
        return {"title": title, "video": rel(video), "script": rel(script_path), "published": False}

    log(f"\n=== [{index}/{target}] 发布抖音 ===")
    publish_cmd = [str(ROOT / "scripts" / "publish-douyin.sh"), rel(video), "--script", rel(script_path)]
    if publish_check:
        publish_cmd.append("--check")
    if dry_run:
        publish_cmd.append("--dry-run")
    run(publish_cmd, label="发布")

    if dry_run:
        return {"title": title, "video": rel(video), "script": rel(script_path), "published": False}

    published = load_published()
    video_rel = rel(video)
    published.add(video_rel)
    save_published(published)
    append_history_from_script(script_path)
    archived = archive_video(video, date_tag=datetime.now().strftime("%Y%m%d"))
    log(f"发布成功，已归档：{rel(archived)}")
    return {"title": title, "video": rel(archived), "script": rel(script_path), "published": True}


def main() -> int:
    load_env()
    os.environ["AIVIDEO_SOURCE"] = "exa"
    parser = argparse.ArgumentParser(description="AI财知道：指定话题一键制作并发布")
    parser.add_argument("topics", nargs="*", help="一段含编号的话题文字；也可用 - 从 stdin 读")
    parser.add_argument("--file", help="从文件读取话题文字")
    parser.add_argument("--days", type=int, default=int(os.environ.get("AIVIDEO_TOPIC_DAYS", "120")),
                        help="搜话题文章的时间窗（天），默认 120")
    parser.add_argument("--check", action="store_true", help="发布前检查抖音登录态")
    parser.add_argument("--dry-run", action="store_true", help="只预演发布参数，不真正发布/归档")
    parser.add_argument("--no-publish", action="store_true", help="只生成视频，跳过发布步骤")
    args = parser.parse_args()

    raw_text = read_topics_text(args).strip()
    if not raw_text:
        log("没有读到任何话题文字。")
        return 1

    topics = specified_topics.parse_topics_input(raw_text)
    if not topics:
        log("未能从输入中解析出话题。")
        return 1

    log(f"解析出 {len(topics)} 个话题：")
    for t in topics:
        kind = "自带内容" if t.get("provided_content") else "搜索/自写"
        log(f"  {t['index']}. {t['title_hint']}  [{kind}]")

    target = len(topics)
    run_start = time.time()
    made: list[dict] = []
    failed: list[dict] = []
    for index, topic in enumerate(topics, 1):
        title_hint = topic["title_hint"]
        log(f"\n>>> 话题 #{index}：{title_hint}")
        try:
            article, details = specified_topics.build_topic_research(topic, days=args.days)
            made.append(
                process_topic(
                    index,
                    target=target,
                    topic=topic,
                    article=article,
                    details=details,
                    publish_check=args.check,
                    dry_run=args.dry_run,
                    skip_publish=args.no_publish,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log(f"\n✗ 话题失败：{title_hint}：{exc}")
            failed.append({"title": title_hint, "error": str(exc)})
            continue

    summary = ROOT / "logs" / "make_topics_last.json"
    summary.write_text(
        json.dumps(
            {
                "input": raw_text,
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
    log(f"\n全部完成：成功 {len(made)}/{target}")
    for item in made:
        log(f"  ✓ {item.get('title')} → {item.get('video')}")
    if failed:
        log(f"\n失败 {len(failed)} 条：")
        for item in failed:
            log(f"  ✗ {item.get('title')} → {item.get('error')}")
    log("\n" + cost_tracker.report_window(run_start, videos=len(made)))
    return 0 if made else 1


if __name__ == "__main__":
    raise SystemExit(main())
