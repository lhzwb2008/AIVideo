#!/usr/bin/env python3
"""发布一次「只生成未发布」批次里的视频（读 logs/make_topics_last.json）。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from batch_aivideo import append_history_from_script
from publish_pipeline import (
    archive_publish_bundle,
    generate_forum_pack,
    log,
    publish_bilibili,
    publish_eastmoney,
    publish_tiktok,
    publish_youtube,
    rel,
)
from publish_caption import print_manual_publish_pack
from paths import ROOT
from research import load_env


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="发布上次 --no-publish 生成的批次")
    parser.add_argument("--summary", default=str(ROOT / "logs" / "make_topics_last.json"))
    parser.add_argument("--dry-run", action="store_true", help="只预演发布参数")
    args = parser.parse_args()

    summary_path = Path(args.summary)
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    made = [m for m in data.get("made") or [] if not m.get("published")]
    if not made:
        log("没有待发布条目。")
        return 0

    log(f"待发布 {len(made)} 条：")
    for m in made:
        log(f"  - {m.get('title')}  ←  {m.get('video')}")

    ok = 0
    for i, m in enumerate(made, 1):
        video = ROOT / m["video"]
        script = ROOT / m["script"]
        if not video.is_file():
            log(f"✗ 视频不存在，跳过：{m['video']}")
            continue
        log(f"\n=== [{i}/{len(made)}] 发布：{m.get('title')} ===")
        generate_forum_pack(script, video)

        youtube_url = publish_youtube(video, script, dry_run=args.dry_run)
        tiktok_url = publish_tiktok(video, script, dry_run=args.dry_run)
        bilibili_title = publish_bilibili(video, script, dry_run=args.dry_run)
        eastmoney_title = ""

        if args.dry_run:
            forum_preview = video.parent / video.stem
            if forum_preview.is_dir() and (forum_preview / "post.md").is_file():
                eastmoney_title = publish_eastmoney(forum_preview, dry_run=True)
            print_manual_publish_pack(
                script,
                video,
                youtube_url=youtube_url,
                tiktok_url=tiktok_url,
                bilibili_title=bilibili_title,
                eastmoney_title=eastmoney_title,
            )
            ok += 1
            continue

        append_history_from_script(script)
        archived = archive_publish_bundle(video, date_tag=datetime.now().strftime("%Y%m%d"))
        if archived.get("forum"):
            eastmoney_title = publish_eastmoney(archived["forum"], dry_run=False)
        print_manual_publish_pack(
            script,
            archived["video"],
            youtube_url=youtube_url,
            tiktok_url=tiktok_url,
            bilibili_title=bilibili_title,
            eastmoney_title=eastmoney_title,
        )
        m["published"] = True
        m["video"] = rel(archived["video"])
        m["youtube_url"] = youtube_url
        m["tiktok_url"] = tiktok_url
        m["bilibili_title"] = bilibili_title
        m["eastmoney_title"] = eastmoney_title
        log(f"已归档：{rel(archived['video'])}")
        if archived.get("forum"):
            log(f"  论坛图文：{rel(archived['forum'])}/")
        ok += 1

    summary_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n完成：成功处理 {ok}/{len(made)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
