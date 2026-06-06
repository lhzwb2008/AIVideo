#!/usr/bin/env python3
"""续跑发布：视频/图文包已生成，补 API 发布 + 归档（跳过生图/合成）。"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from batch_aivideo import append_history_from_script
from paths import ROOT
from publish_caption import print_manual_publish_pack
from publish_pipeline import (
    archive_publish_bundle,
    log,
    publish_bilibili,
    publish_eastmoney,
    publish_tiktok,
    publish_wechat,
    publish_xueqiu,
    publish_youtube,
    rel,
)
from publish_resolve import resolve_script_for_video
from research import load_env


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="续跑：仅发布与归档（成片已存在）")
    parser.add_argument("--video", required=True, help="MP4 路径")
    parser.add_argument("--script", help="脚本 JSON，默认同目录 logs 或 resolve")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--skip-bilibili-video",
        action="store_true",
        help="跳过 biliup 视频上传（视频已手动/重复发过时用）",
    )
    args = parser.parse_args()
    skip_bili_video = args.skip_bilibili_video

    video = Path(args.video)
    if not video.is_absolute():
        video = ROOT / video
    if not video.is_file():
        log(f"✗ 视频不存在：{video}")
        return 1

    script_path = resolve_script_for_video(video, args.script)
    forum_dir = video.parent / video.stem
    if not (forum_dir / "post.md").is_file():
        forum_dir = None

    log(f"续发：{rel(video)}")
    log(f"脚本：{rel(script_path)}")
    if forum_dir:
        log(f"图文包：{rel(forum_dir)}/")

    youtube_url = publish_youtube(video, script_path, dry_run=args.dry_run)
    tiktok_url = publish_tiktok(video, script_path, dry_run=args.dry_run)
    if skip_bili_video:
        log("B站：跳过视频上传（--skip-bilibili-video）")
    bilibili_title = publish_bilibili(
        video,
        script_path,
        dry_run=args.dry_run,
        skip_video=skip_bili_video,
    )
    wechat_title = ""
    if forum_dir:
        wechat_title = publish_wechat(forum_dir, dry_run=args.dry_run)

    if args.dry_run:
        eastmoney_title = publish_eastmoney(forum_dir, dry_run=True) if forum_dir else ""
        xueqiu_title = publish_xueqiu(forum_dir, dry_run=True) if forum_dir else ""
        print_manual_publish_pack(
            script_path,
            video,
            youtube_url=youtube_url,
            tiktok_url=tiktok_url,
            bilibili_title=bilibili_title,
            eastmoney_title=eastmoney_title,
            xueqiu_title=xueqiu_title,
            wechat_title=wechat_title,
        )
        return 0

    append_history_from_script(script_path)
    archived = archive_publish_bundle(video, date_tag=datetime.now().strftime("%Y%m%d"))
    log(f"已归档：{rel(archived['video'])}")
    eastmoney_title = ""
    xueqiu_title = ""
    if archived.get("forum"):
        log(f"  论坛图文：{rel(archived['forum'])}/")
        eastmoney_title = publish_eastmoney(archived["forum"], dry_run=False)
        xueqiu_title = publish_xueqiu(archived["forum"], dry_run=False)

    print_manual_publish_pack(
        script_path,
        archived["video"],
        youtube_url=youtube_url,
        tiktok_url=tiktok_url,
        bilibili_title=bilibili_title,
        eastmoney_title=eastmoney_title,
        xueqiu_title=xueqiu_title,
        wechat_title=wechat_title,
    )
    log("续发完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
