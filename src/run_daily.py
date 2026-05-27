#!/usr/bin/env python3
"""每日定时编排：搜索近 24h AI 圈中英文热点 → 生成 2 个视频 → 发布抖音 → 归档已发布视频。

逻辑：
1. 重置 batch_progress（每天一个全新批次）
2. 调 `run-batch-aivideo.sh --count 2 --days 1` 生成 2 个视频
3. 调 `publish-all-douyin.sh` 发布
4. 读取 published_videos.json，把今天成功发布的 mp4 移到 output/published/YYYYMMDD/
   —— 让下一天 output/ 下只剩待发布的（或为空）

非 0 退出码用于让 launchd / cron 标记失败。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from paths import ROOT

LOGS = ROOT / "logs"
OUTPUT_DIR = ROOT / "output"
PUBLISHED_DIR = OUTPUT_DIR / "published"
BATCH_PROGRESS = LOGS / "batch_progress.json"
PUBLISHED_LOG = LOGS / "published_videos.json"
DAILY_LOG = LOGS / "daily_run.log"


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    DAILY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with DAILY_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd: list[str], *, label: str) -> int:
    log(f"▶ {label}: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT)
    log(f"  ↳ {label} 退出码 {proc.returncode}")
    return proc.returncode


def load_published_set() -> set[str]:
    if not PUBLISHED_LOG.is_file():
        return set()
    try:
        data = json.loads(PUBLISHED_LOG.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    items = data if isinstance(data, list) else data.get("videos", [])
    return {str(v) for v in items}


def archive_published(date_tag: str) -> int:
    """把 published_videos.json 里指向 output/*.mp4 的视频挪到 output/published/<date_tag>/。
    返回挪走的数量。"""
    if not PUBLISHED_LOG.is_file():
        return 0
    published = load_published_set()
    if not published:
        return 0
    dest = PUBLISHED_DIR / date_tag
    dest.mkdir(parents=True, exist_ok=True)

    moved = 0
    new_set: list[str] = []
    for rel in sorted(published):
        src = ROOT / rel
        # 已经在 published/ 子目录下的保持原样
        if not src.is_file() or not src.parent.samefile(OUTPUT_DIR):
            new_set.append(rel)
            continue
        target = dest / src.name
        if target.exists():
            target = dest / f"{src.stem}_{datetime.now().strftime('%H%M%S')}{src.suffix}"
        shutil.move(str(src), str(target))
        new_rel = str(target.resolve().relative_to(ROOT.resolve()))
        new_set.append(new_rel)
        log(f"  📦 归档 {rel} → {new_rel}")
        moved += 1

    if moved:
        PUBLISHED_LOG.write_text(
            json.dumps(
                {
                    "videos": sorted(new_set),
                    "updated_at": datetime.now().astimezone().isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    return moved


def main() -> int:
    parser = argparse.ArgumentParser(description="AIVideo 每日定时编排（制作 2 条 + 发布 + 归档）")
    parser.add_argument("--count", type=int, default=2, help="今日制作几条视频（默认 2）")
    parser.add_argument("--days", type=int, default=1, help="搜索时间窗，默认近 1 天")
    parser.add_argument("--skip-publish", action="store_true", help="只制作不发布（调试用）")
    parser.add_argument("--skip-make", action="store_true", help="只发布+归档（调试用）")
    args = parser.parse_args()

    date_tag = datetime.now().strftime("%Y%m%d")
    log("=" * 60)
    log(f"AIVideo 每日任务 · {date_tag} · count={args.count} days={args.days}")
    log("=" * 60)

    if not args.skip_make:
        # 重置 batch 进度，确保是干净的一批
        if BATCH_PROGRESS.is_file():
            BATCH_PROGRESS.unlink()
            log("已清空 batch_progress.json，开始新批次")

        rc = run(
            [str(ROOT / "run-batch-aivideo.sh"),
             "--count", str(args.count),
             "--days", str(args.days),
             "--source", os.environ.get("AIVIDEO_SOURCE", "feeds"),
             "--fresh-hours", os.environ.get("AIVIDEO_FRESH_HOURS", "24"),
             "--reset"],
            label="制作批量视频",
        )
        if rc != 0:
            log("✗ 制作失败，跳过发布")
            return rc

    if args.skip_publish:
        log("仅制作（--skip-publish），不发布。")
        return 0

    rc = run(
        [str(ROOT / "publish-all-douyin.sh")],
        label="发布抖音",
    )
    if rc != 0:
        log("✗ 发布脚本返回非 0，仍尝试归档已成功发布的视频")

    moved = archive_published(date_tag)
    log(f"归档完成：今日移动 {moved} 个已发布视频到 output/published/{date_tag}/")
    log("=" * 60 + "\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
