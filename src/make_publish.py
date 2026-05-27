#!/usr/bin/env python3
"""一键制作并发布：Exa 选题 → 改编 → 生图 → 合成 → 抖音发布 → 归档。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from batch_aivideo import append_history_from_script, history_exclude_urls, history_recent_topics
from paths import ROOT
from publish_all_douyin import load_published, save_published
from research import find_articles, load_env, run_article_research, score_articles


def log(message: str) -> None:
    print(message, flush=True)


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve()))


def run(cmd: list[str], *, label: str) -> None:
    log(f"\n[{label}] {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT, env=os.environ.copy())
    if proc.returncode != 0:
        raise RuntimeError(f"{label} 失败，退出码 {proc.returncode}")


def read_script_title(script_path: Path) -> str:
    try:
        data = json.loads(script_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    script = data.get("script") or data
    return str(script.get("title") or "").strip()


def latest_video() -> Path:
    last_video = ROOT / "logs" / "last_video.txt"
    if not last_video.is_file():
        raise RuntimeError("未找到 logs/last_video.txt")
    raw = last_video.read_text(encoding="utf-8").strip()
    video = Path(raw)
    if not video.is_absolute():
        video = ROOT / video
    if not video.is_file():
        raise RuntimeError(f"视频文件不存在: {video}")
    return video


def archive_video(video: Path, *, date_tag: str) -> Path:
    dest_dir = ROOT / "archive" / "published" / date_tag
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / video.name
    if target.exists():
        target = dest_dir / f"{video.stem}_{datetime.now().strftime('%H%M%S')}{video.suffix}"
    shutil.move(str(video), str(target))
    return target


def select_topics(*, days: int, limit: int) -> list[dict]:
    exclude = history_exclude_urls()
    recent_topics = history_recent_topics()
    if recent_topics:
        log(f"已加载历史标题 {len(recent_topics)} 条用于去重")

    log(f"\n=== 选题打分：Exa 近 {days} 天，最多制作 {limit} 条 ===")
    candidates, _ = find_articles(
        days=days,
        exclude_urls=exclude,
        recent_topics=recent_topics,
        source="exa",
    )
    scored, decision = score_articles(candidates, recent_topics=recent_topics)
    selected = scored[:limit]

    report = ROOT / "logs" / "make_publish_topics.json"
    report.write_text(
        json.dumps(
            {
                "days": days,
                "limit": limit,
                "threshold": decision.get("threshold"),
                "candidate_count": len(candidates),
                "accepted_count": len(scored),
                "selected": selected,
                "decision": decision,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    log(f"选题完成：{len(candidates)} 个候选，{len(scored)} 个过线，准备制作 {len(selected)} 条")
    for i, topic in enumerate(selected, 1):
        log(f"  {i}. [{topic.get('topic_score')}] {topic.get('question_title') or topic.get('title')}")
    return selected


def process_one(
    index: int,
    *,
    total: int,
    days: int,
    publish_check: bool,
    dry_run: bool,
    article: dict,
) -> dict:
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    script_path = logs_dir / f"last_script_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{index:02d}.json"

    log(f"\n=== [{index}/{total}] 制作视频 ===")
    script, _ = run_article_research(
        output=script_path,
        days=days,
        auto_pick=True,
        recent_topics=history_recent_topics(),
        source="exa",
        preselected_article=article,
    )
    title = str(script.get("title") or read_script_title(script_path) or "").strip()
    log(f"选题脚本：{title}")

    run([str(ROOT / "scripts" / "run-enrich-images.sh"), str(script_path)], label="生图")
    run([str(ROOT / "scripts" / "run-compose.sh"), str(script_path)], label="合成")
    video = latest_video()

    log(f"\n=== [{index}/{total}] 发布抖音 ===")
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
    log(f"发布成功，已记录标题并归档：{rel(archived)}")
    return {
        "title": title,
        "video": rel(archived),
        "script": rel(script_path),
        "published": True,
    }


def main() -> int:
    load_env()
    os.environ["AIVIDEO_SOURCE"] = "exa"
    parser = argparse.ArgumentParser(description="AI财知道：一键制作并自动发布")
    parser.add_argument("--count", type=int, default=int(os.environ.get("AIVIDEO_MAX_VIDEOS_PER_RUN", "5")))
    parser.add_argument("--days", type=int, default=int(os.environ.get("AIVIDEO_DAYS", os.environ.get("DAILY_RUN_DAYS", "7"))))
    parser.add_argument("--check", action="store_true", help="发布前检查抖音登录态")
    parser.add_argument("--dry-run", action="store_true", help="只预演发布参数，不真正发布/归档")
    parser.add_argument("--continue-on-error", action="store_true", help="单条失败后继续下一条")
    args = parser.parse_args()

    made: list[dict] = []
    failed: list[dict] = []
    topics = select_topics(days=args.days, limit=args.count)
    if not topics:
        log("没有过线选题，本次不制作。")
        return 0
    for i, article in enumerate(topics, 1):
        try:
            made.append(
                process_one(
                    i,
                    total=len(topics),
                    days=args.days,
                    publish_check=args.check,
                    dry_run=args.dry_run,
                    article=article,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log(f"\n✗ [{i}/{len(topics)}] 单条失败：{exc}")
            failed.append({
                "title": article.get("question_title") or article.get("title"),
                "url": article.get("url"),
                "score": article.get("topic_score"),
                "error": str(exc),
            })
            if not args.continue_on_error:
                log("已终止：请先修复失败原因，再继续运行。")
                return 1
            continue

    summary = ROOT / "logs" / "make_publish_last.json"
    summary.write_text(
        json.dumps(
            {"items": made, "failed": failed, "updated_at": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log("\n全部完成：")
    for item in made:
        log(f"  - {item.get('title')} → {item.get('video')}")
    if failed:
        log("\n失败条目：")
        for item in failed:
            log(f"  - [{item.get('score')}] {item.get('title')} → {item.get('error')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
