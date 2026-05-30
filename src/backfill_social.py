#!/usr/bin/env python3
"""把「抖音已发布」的存量视频批量补发到 小红书 / 快手 / 视频号。

数据来源：
- logs/published_videos.json  抖音已发布清单（发布时的 output/ 相对路径）
- logs/video_manifest.jsonl   视频→脚本映射（用于生成文案）
实际文件可能已归档到 archive/published/<date>/，按文件名自动定位。

每个平台用独立的 logs/published_<platform>.json 记录已补发，避免重复。
单条发布通过 scripts/publish-social.sh 子进程完成（在 SAU venv 中运行）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from paths import ROOT

PUBLISHED_DOUYIN = ROOT / "logs" / "published_videos.json"
MANIFEST = ROOT / "logs" / "video_manifest.jsonl"

PLATFORM_LABEL = {"xiaohongshu": "小红书", "kuaishou": "快手", "shipinhao": "视频号"}


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def load_douyin_published() -> list[str]:
    if not PUBLISHED_DOUYIN.is_file():
        return []
    data = json.loads(PUBLISHED_DOUYIN.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else data.get("videos", [])


def build_script_map() -> dict[str, str]:
    """按视频文件名 → 脚本路径。仅采用「带时间戳的脚本」，过滤掉会被覆盖的
    通用 logs/last_script.json，避免给老视频套上错误文案。"""
    by_base: dict[str, str] = {}
    if not MANIFEST.is_file():
        return by_base
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        video, script = row.get("video"), row.get("script")
        if not (video and script):
            continue
        sp = Path(script)
        if sp.name == "last_script.json":  # 通用文件，内容已被覆盖，不可靠
            continue
        by_base[Path(video).name] = script
    return by_base


def locate_video(rel_or_name: str) -> Path | None:
    """published 里存的是 output/ 路径，实际文件可能已归档。按文件名定位。"""
    name = Path(rel_or_name).name
    direct = ROOT / "output" / name
    if direct.is_file():
        return direct
    matches = list((ROOT / "archive" / "published").glob(f"*/{name}"))
    if matches:
        return matches[0]
    p = ROOT / rel_or_name
    return p if p.is_file() else None


def published_log_path(platform: str) -> Path:
    return ROOT / "logs" / f"published_{platform}.json"


def load_platform_published(platform: str) -> set[str]:
    path = published_log_path(platform)
    if not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else data.get("videos", [])
    return {Path(v).name for v in items}


def save_platform_published(platform: str, names: set[str]) -> None:
    path = published_log_path(platform)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"videos": sorted(names), "updated_at": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def publish_one(platform: str, video: Path, script: Path, extra: list[str]) -> int:
    cmd = [
        str(ROOT / "scripts" / "publish-social.sh"),
        platform,
        str(video.relative_to(ROOT)),
        "--script",
        str(script.relative_to(ROOT)),
        *extra,
    ]
    print(f"\n>>> [{PLATFORM_LABEL.get(platform, platform)}] 发布 {video.name}", flush=True)
    return subprocess.run(cmd, cwd=ROOT).returncode


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="抖音存量视频批量补发到其它平台")
    parser.add_argument("platform", nargs="?", default="xiaohongshu",
                        help="xiaohongshu | kuaishou | shipinhao（默认小红书）")
    parser.add_argument("--dry-run", action="store_true", help="只列出补发计划")
    parser.add_argument("--headed", action="store_true", help="有头 Chrome")
    parser.add_argument("--limit", type=int, default=0, help="本次最多补发条数（0=不限）")
    parser.add_argument("--sleep", type=int, default=120,
                        help="两条之间的基准间隔秒数（默认 120，会再叠加 ±50%% 随机抖动，降低风控）")
    parser.add_argument("--force", action="store_true", help="忽略已补发记录，重发")
    args = parser.parse_args()

    platform = args.platform.strip().lower()
    if platform not in PLATFORM_LABEL:
        print(f"未知平台: {platform}（可选: {', '.join(PLATFORM_LABEL)}）", file=sys.stderr)
        return 2

    douyin_pub = load_douyin_published()
    if not douyin_pub:
        print("logs/published_videos.json 为空，没有抖音已发布记录。")
        return 0

    script_map = build_script_map()
    already = set() if args.force else load_platform_published(platform)

    pending: list[tuple[Path, Path]] = []
    skipped: list[tuple[str, str]] = []
    for rel in douyin_pub:
        name = Path(rel).name
        if name in already:
            continue
        video = locate_video(rel)
        if not video:
            skipped.append((name, "视频文件未找到（output/ 和 archive/ 都没有）"))
            continue
        script_rel = script_map.get(name)
        if not script_rel:
            skipped.append((name, "无可靠脚本映射（早期通用 last_script.json，已被覆盖）"))
            continue
        script = Path(script_rel)
        if not script.is_absolute():
            script = ROOT / script
        if not script.is_file():
            skipped.append((name, f"脚本文件不存在: {script_rel}"))
            continue
        pending.append((video, script))

    label = PLATFORM_LABEL[platform]
    print(f"抖音已发布 {len(douyin_pub)} 条；已补发到{label} {len(already)} 条。")
    print(f"待补发 {len(pending)} 条；跳过 {len(skipped)} 条。\n")
    for video, script in pending:
        print(f"  ✓ {video.name}  ←  {script.name}")
    if skipped:
        print("\n跳过（需手动处理）：")
        for name, why in skipped:
            print(f"  - {name}: {why}")

    if args.limit and len(pending) > args.limit:
        pending = pending[: args.limit]
        print(f"\n（--limit {args.limit}：本次只发前 {len(pending)} 条）")

    if args.dry_run:
        print("\n（dry-run，未实际发布）")
        return 0

    if not pending:
        print("\n没有需要补发的视频。")
        return 0

    extra = ["--headed"] if args.headed else []
    ok = fail = 0
    for i, (video, script) in enumerate(pending):
        rc = publish_one(platform, video, script, extra)
        if rc == 0:
            ok += 1
            already.add(video.name)
            save_platform_published(platform, already)
        else:
            fail += 1
            print(f"  ✗ {video.name} 失败（rc={rc}），继续下一条…", flush=True)
        if args.sleep and i < len(pending) - 1:
            jitter = random.uniform(0.5, 1.5)  # 基准 ±50% 抖动，模拟真人节奏
            wait_s = max(1, int(args.sleep * jitter))
            print(f"  ⏳ 间隔 {wait_s}s 后发下一条…", flush=True)
            time.sleep(wait_s)

    print(f"\n完成：成功 {ok}，失败 {fail}，跳过 {len(skipped)}。")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
