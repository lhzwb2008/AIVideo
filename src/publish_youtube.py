#!/usr/bin/env python3
"""发布 MP4 到 YouTube（Shorts：竖屏 + #Shorts 描述信号）。

子命令：
  login    OAuth 授权（首次 / 续期）
  check    校验 token 并显示频道名
  publish  上传单条视频

独立使用：
  ./setup-youtube.sh
  ./youtube-login.sh
  ./scripts/publish-youtube.sh output/xxx.mp4 --script logs/xxx.json
  ./scripts/publish-youtube.sh --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from paths import ROOT
from youtube_auth import YouTubeAuthError, account_name, run_check, run_login
from youtube_caption import build_youtube_fields
from publish_resolve import load_script, resolve_cover_image, resolve_script_for_video
from youtube_publisher import (
    YouTubePublishError,
    update_privacy,
    update_video_metadata,
    upload_video,
)


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


def resolve_video(path: str | None) -> Path:
    if path:
        video = Path(path)
        if not video.is_absolute():
            video = ROOT / video
        if not video.is_file():
            raise YouTubePublishError(f"视频不存在: {video}")
        return video.resolve()

    from locale_env import latest_output_video

    latest = latest_output_video()
    if latest:
        return latest.resolve()
    raise YouTubePublishError("output/{locale}/ 下没有 mp4")


def _last_video_path() -> Path | None:
    log_path = ROOT / "logs" / "last_youtube_publish.json"
    if not log_path.is_file():
        return None
    data = json.loads(log_path.read_text(encoding="utf-8"))
    raw = data.get("video") or ""
    if not raw:
        return None
    p = Path(raw)
    if not p.is_absolute():
        p = ROOT / p
    return p if p.is_file() else None


def main() -> int:
    load_env()

    parser = argparse.ArgumentParser(description="YouTube Shorts 发布（Data API v3）")
    sub = parser.add_subparsers(dest="action", required=True)

    p_login = sub.add_parser("login", help="OAuth 授权")
    p_login.add_argument("--force", action="store_true", help="清除旧 token 重新授权")

    sub.add_parser("check", help="校验 token 并显示频道")

    p_pub = sub.add_parser("publish", help="上传视频")
    p_pub.add_argument("video", nargs="?", help="MP4 路径，默认 output 下最新")
    p_pub.add_argument("--script", help="脚本 JSON")
    p_pub.add_argument("--title", help="覆盖标题")
    p_pub.add_argument("--desc", help="覆盖描述")
    p_pub.add_argument("--tags", help="覆盖标签（逗号分隔）")
    p_pub.add_argument("--thumbnail", help="封面 PNG")
    p_pub.add_argument(
        "--privacy",
        choices=("public", "private", "unlisted"),
        help="覆盖 YOUTUBE_PRIVACY",
    )
    p_pub.add_argument("--dry-run", action="store_true", help="只打印参数不上传")

    p_vis = sub.add_parser("set-privacy", help="修改已上传视频的可见性")
    p_vis.add_argument("video_id", help="YouTube video id，或填 last 读上次记录")
    p_vis.add_argument(
        "--privacy",
        default="public",
        choices=("public", "private", "unlisted"),
        help="目标可见性（默认 public）",
    )

    p_fix = sub.add_parser("fix-last", help="按脚本修正上次上传的标题与封面")
    p_fix.add_argument("--script", help="脚本 JSON（默认自动匹配视频）")
    p_fix.add_argument("--video", help="MP4 路径（默认读 last_youtube_publish.json）")
    p_fix.add_argument("--thumb-only", action="store_true", help="只更新封面，不改标题/描述")

    args = parser.parse_args()

    try:
        if args.action == "login":
            return run_login(force=args.force)
        if args.action == "check":
            return run_check()
        if args.action == "set-privacy":
            vid = args.video_id.strip()
            if vid.lower() == "last":
                log_path = ROOT / "logs" / "last_youtube_publish.json"
                if not log_path.is_file():
                    raise YouTubePublishError(f"未找到记录: {log_path}")
                vid = json.loads(log_path.read_text(encoding="utf-8")).get("video_id") or ""
                if not vid:
                    raise YouTubePublishError("记录里没有 video_id")
            print(f"将 {vid} 设为 {args.privacy}…", flush=True)
            result = update_privacy(vid, privacy_status=args.privacy)
            print(f"✅ 已更新: {result['url']}（{result['privacy']}）", flush=True)
            return 0
        if args.action == "fix-last":
            log_path = ROOT / "logs" / "last_youtube_publish.json"
            if not log_path.is_file():
                raise YouTubePublishError(f"未找到: {log_path}")
            last = json.loads(log_path.read_text(encoding="utf-8"))
            vid = last.get("video_id") or ""
            if not vid:
                raise YouTubePublishError("记录里没有 video_id")
            video_path = Path(args.video) if args.video else _last_video_path()
            if not video_path or not video_path.is_file():
                raise YouTubePublishError("找不到视频文件，请 --video 指定")
            if not video_path.is_absolute():
                video_path = ROOT / video_path
            script_path = resolve_script_for_video(video_path.resolve(), args.script)
            script = load_script(script_path)
            if not script:
                raise YouTubePublishError("未找到脚本，请 --script 指定")
            fields = build_youtube_fields(script)
            thumb = resolve_cover_image(script_path, video_path.resolve())
            print(f"脚本: {script_path}", flush=True)
            if not args.thumb_only:
                print(f"标题: {fields['title']}", flush=True)
            print(f"封面: {thumb}", flush=True)
            update_video_metadata(
                vid,
                title=None if args.thumb_only else fields["title"],
                description=None if args.thumb_only else fields["description"],
                tags=None if args.thumb_only else fields["tags"],
                thumbnail_path=thumb,
            )
            print(f"✅ 已更新: https://www.youtube.com/watch?v={vid}", flush=True)
            return 0

        video_path = resolve_video(args.video)
        script_path = resolve_script_for_video(video_path, args.script)
        script = load_script(script_path)
        fields = build_youtube_fields(script)

        title = args.title or fields["title"]
        description = args.desc if args.desc is not None else fields["description"]
        if args.tags is not None:
            tags = [t.strip().lstrip("#") for t in args.tags.split(",") if t.strip()]
        else:
            tags = fields["tags"]

        thumbnail = (
            Path(args.thumbnail).resolve()
            if args.thumbnail
            else resolve_cover_image(script_path, video_path)
        )

        if script_path:
            print(f"脚本: {script_path}", flush=True)
        elif not args.title:
            print(
                f"警告: 未找到 {video_path.name} 的脚本，标题/封面可能不完整。"
                " 可用 --script 指定。",
                file=sys.stderr,
            )

        print(f"账号: {account_name()}", flush=True)
        print(f"视频: {video_path}", flush=True)
        print(f"标题: {title}", flush=True)
        print(f"描述:\n{description}", flush=True)
        print(f"标签: {tags}", flush=True)
        if thumbnail:
            print(f"封面: {thumbnail}", flush=True)

        if args.dry_run:
            print("（dry-run，不实际上传）", flush=True)
            return 0

        print("开始上传到 YouTube…", flush=True)
        result = upload_video(
            video_path,
            title=title,
            description=description,
            tags=tags,
            privacy_status=args.privacy,
            thumbnail_path=thumbnail,
        )

        log_path = ROOT / "logs" / "last_youtube_publish.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps(
                {
                    "account": account_name(),
                    "video": str(video_path),
                    "video_id": result["video_id"],
                    "url": result["url"],
                    "shorts_url": result["shorts_url"],
                    "title": title,
                    "script": str(script_path) if script_path else "",
                    "thumbnail": str(thumbnail) if thumbnail else "",
                    "privacy": result["privacy"],
                    "published_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"✅ 上传成功: {result['url']}", flush=True)
        print(f"   Shorts: {result['shorts_url']}", flush=True)
        print(f"   记录: {log_path}", flush=True)
        if result["privacy"] != "public":
            print("   提示: 非 public 时仅自己可见。", flush=True)
        return 0
    except (YouTubeAuthError, YouTubePublishError) as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
