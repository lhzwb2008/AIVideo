#!/usr/bin/env python3
"""发布 MP4 到 TikTok（Content Posting API Direct Post）。

子命令：
  login    OAuth 授权（Desktop + PKCE）
  check    校验 token 并显示创作者信息
  publish  上传单条视频
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from paths import ROOT
from publish_resolve import load_script, resolve_script_for_video
from tiktok_auth import TikTokAuthError, account_name, run_check, run_login
from tiktok_caption import build_tiktok_fields
from tiktok_publisher import TikTokPublishError, upload_video


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
            raise TikTokPublishError(f"视频不存在: {video}")
        return video.resolve()

    from locale_env import latest_output_video

    latest = latest_output_video()
    if latest:
        return latest.resolve()
    raise TikTokPublishError("output/{locale}/ 下没有 mp4")


def main() -> int:
    load_env()

    parser = argparse.ArgumentParser(description="TikTok Direct Post 发布")
    sub = parser.add_subparsers(dest="action", required=True)

    p_login = sub.add_parser("login", help="OAuth 授权")
    p_login.add_argument("--force", action="store_true", help="清除旧 token 重新授权")

    sub.add_parser("check", help="校验 token")

    p_pub = sub.add_parser("publish", help="上传视频")
    p_pub.add_argument("video", nargs="?", help="MP4 路径，默认 output 下最新")
    p_pub.add_argument("--script", help="脚本 JSON")
    p_pub.add_argument("--title", help="覆盖 caption")
    p_pub.add_argument(
        "--privacy",
        choices=("PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR", "SELF_ONLY"),
        help="覆盖 TIKTOK_PRIVACY",
    )
    p_pub.add_argument("--dry-run", action="store_true", help="只打印参数不上传")

    args = parser.parse_args()

    try:
        if args.action == "login":
            return run_login(force=args.force)
        if args.action == "check":
            return run_check()

        video_path = resolve_video(args.video)
        script_path = resolve_script_for_video(video_path, args.script)
        script = load_script(script_path)
        fields = build_tiktok_fields(script)
        title = args.title or fields["title"]

        if script_path:
            print(f"脚本: {script_path}", flush=True)
        elif not args.title:
            print(
                f"警告: 未找到 {video_path.name} 的脚本，caption 可能不完整。"
                " 可用 --script 指定。",
                file=sys.stderr,
            )

        print(f"账号: {account_name()}", flush=True)
        print(f"视频: {video_path}", flush=True)
        print(f"Caption:\n{title}", flush=True)
        print(f"标签: {fields['tags']}", flush=True)

        if args.dry_run:
            print("（dry-run，不实际上传）", flush=True)
            return 0

        print("开始上传到 TikTok…", flush=True)
        result = upload_video(video_path, title=title, privacy_level=args.privacy)

        log_path = ROOT / "logs" / "last_tiktok_publish.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps(
                {
                    "account": account_name(),
                    "video": str(video_path),
                    "publish_id": result["publish_id"],
                    "post_id": result["post_id"],
                    "url": result["url"],
                    "privacy": result["privacy"],
                    "username": result["username"],
                    "title": title,
                    "script": str(script_path) if script_path else "",
                    "published_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        if result["url"]:
            print(f"✅ 上传成功: {result['url']}", flush=True)
        elif result.get("mode") == "inbox":
            print(
                f"✅ 已上传到 TikTok 收件箱 publish_id={result['publish_id']}",
                flush=True,
            )
            print("   请打开 TikTok App → Inbox/收件箱，粘贴下方文案后发布。", flush=True)
            print("\n── TikTok 发布文案（复制粘贴）──", flush=True)
            print(title, flush=True)
            print("────────────────────────────", flush=True)
        else:
            print(
                f"✅ 发布完成 publish_id={result['publish_id']} privacy={result['privacy']}",
                flush=True,
            )
            print("   未审核应用或 moderation 未完成时可能暂无公开链接。", flush=True)
        print(f"   记录: {log_path}", flush=True)
        return 0
    except (TikTokAuthError, TikTokPublishError) as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
