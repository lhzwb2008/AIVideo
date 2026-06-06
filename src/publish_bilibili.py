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
from sau_client import (
    SauError,
    bilibili_account,
    bilibili_video_upload_skippable,
    check_bilibili_session,
    publish_bilibili_video,
)


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
    parser.add_argument(
        "--skip-video",
        action="store_true",
        help="跳过 biliup 视频上传（视频已手动/重复发过时用）",
    )
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

        video_uploaded = False
        video_skipped = bool(args.skip_video)
        skip_reason = ""
        if args.skip_video:
            print("跳过视频上传（--skip-video）", flush=True)
            skip_reason = "skip-video"
        else:
            print("开始上传（biliup，视网速约 2–10 分钟）…", flush=True)
            try:
                publish_bilibili_video(
                    video_path,
                    title=title,
                    desc=desc,
                    tags=tags,
                    tid=tid,
                    root=ROOT,
                )
                video_uploaded = True
            except SauError as exc:
                if bilibili_video_upload_skippable(str(exc)):
                    video_skipped = True
                    skip_reason = "rate-limit-or-duplicate"
                    print(
                        "  ⚠️ B站视频上传跳过：投稿过于频繁或重复投稿（视为视频已发过）",
                        flush=True,
                    )
                else:
                    raise

        log_path = ROOT / "logs" / "last_bilibili_publish.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)

        log_payload = {
            "method": "biliup",
            "account": bilibili_account(),
            "video": str(video_path),
            "title": title,
            "desc": desc,
            "tags": tags,
            "tid": tid,
            "video_uploaded": video_uploaded,
            "video_skipped": video_skipped,
            "video_skip_reason": skip_reason,
            "published_at": datetime.now(timezone.utc).isoformat(),
        }

        log_path.write_text(
            json.dumps(log_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  记录: {log_path}")
        return 0
    except (SauError, BilibiliPublishError, RuntimeError, FileNotFoundError) as exc:
        print(str(exc), file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
