#!/usr/bin/env python3
"""发布 MP4 到 B 站创作中心（social-auto-upload / biliup CLI）。"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from bilibili_caption import build_bilibili_fields
from paths import ROOT
from publish_resolve import load_script, resolve_script_for_video
from research import load_env
from sau_client import SauError, bilibili_account, check_bilibili_session, publish_bilibili_video


class BilibiliPublishError(RuntimeError):
    pass


def resolve_video(path: str | None) -> Path:
    if path:
        video = Path(path)
        if not video.is_absolute():
            video = ROOT / video
        if not video.is_file():
            raise BilibiliPublishError(f"视频不存在: {video}")
        return video.resolve()

    last_video = ROOT / "logs" / "last_video.txt"
    if last_video.is_file():
        raw = last_video.read_text(encoding="utf-8").strip()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = ROOT / candidate
        if candidate.is_file():
            return candidate.resolve()

    output_dir = ROOT / "output"
    candidates = sorted(output_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise BilibiliPublishError("未找到可发布的 mp4（output/ 或 logs/last_video.txt）")
    return candidates[0].resolve()


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="发布视频到 B 站（biliup）")
    parser.add_argument("video", nargs="?", help="MP4 路径，默认 output 下最新或 last_video.txt")
    parser.add_argument("--script", help="脚本 JSON")
    parser.add_argument("--title", help="覆盖标题")
    parser.add_argument("--desc", help="覆盖简介")
    parser.add_argument("--tags", help="覆盖标签（逗号分隔）")
    parser.add_argument("--tid", type=int, help="覆盖分区 tid（默认 207=财经商业）")
    parser.add_argument("--dry-run", action="store_true", help="只打印参数，不实际上传")
    parser.add_argument("--check", action="store_true", help="发布前先校验登录态")
    args = parser.parse_args()

    try:
        video_path = resolve_video(args.video)
        script_path = resolve_script_for_video(video_path, args.script)
        script = load_script(script_path)
        fields = build_bilibili_fields(script)

        title = args.title or fields["title"]
        desc = args.desc or fields["desc"]
        tags = args.tags if args.tags is not None else fields["tags"]
        tid = args.tid if args.tid is not None else fields["tid"]

        print(f"账号: {bilibili_account()}")
        print(f"视频: {video_path}")
        print(f"标题: {title}")
        print(f"简介: {desc[:120]}{'…' if len(desc) > 120 else ''}")
        print(f"标签: {tags or '(无)'}")
        print(f"分区 tid: {tid}")

        if args.dry_run:
            return 0

        if args.check:
            print("检查 B 站登录态…", flush=True)
            check_bilibili_session(root=ROOT)
            print("登录态有效", flush=True)

        print("开始上传（biliup，视网速约 2–10 分钟）…", flush=True)
        publish_bilibili_video(
            video_path,
            title=title,
            desc=desc,
            tags=tags,
            tid=tid,
            root=ROOT,
        )

        log_path = ROOT / "logs" / "last_bilibili_publish.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps(
                {
                    "method": "biliup",
                    "account": bilibili_account(),
                    "video": str(video_path),
                    "title": title,
                    "desc": desc,
                    "tags": tags,
                    "tid": tid,
                    "published_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("B 站稿件已提交（审核期间通常仅自己可见）")
        print(f"  记录: {log_path}")
        return 0
    except (SauError, BilibiliPublishError, RuntimeError, FileNotFoundError) as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
