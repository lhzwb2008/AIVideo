#!/usr/bin/env python3
"""发布一次「只生成未发布」批次里的视频（读 logs/make_topics_last.json）。

用于先 --no-publish 生成、确认无误后再统一发布的场景：
  python3 src/publish_made.py
  python3 src/publish_made.py --check
发布成功后：抖音记录、归档、联动 YouTube / 小红书等。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from batch_aivideo import append_history_from_script
from publish_all_douyin import load_published, save_published
from publish_pipeline import (
    archive_video,
    log,
    publish_social,
    publish_youtube,
    rel,
    run,
)
from paths import ROOT
from research import load_env


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="发布上次 --no-publish 生成的批次")
    parser.add_argument("--summary", default=str(ROOT / "logs" / "make_topics_last.json"))
    parser.add_argument("--check", action="store_true", help="发布前校验抖音登录态")
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
        cmd = [str(ROOT / "scripts" / "publish-douyin.sh"), rel(video), "--script", rel(script)]
        if args.check:
            cmd.append("--check")
        if args.dry_run:
            cmd.append("--dry-run")
        try:
            run(cmd, label="发布抖音")
        except Exception as exc:  # noqa: BLE001
            log(f"✗ 抖音发布失败：{exc}")
            continue

        youtube_url = ""
        if args.dry_run:
            youtube_url = publish_youtube(video, script, dry_run=True)
            ok += 1
            continue

        published = load_published()
        published.add(rel(video))
        save_published(published)
        append_history_from_script(script)
        archived = archive_video(video, date_tag=datetime.now().strftime("%Y%m%d"))
        log(f"抖音发布成功，已归档：{rel(archived)}")

        log("联动发布其它平台…")
        publish_social(archived, script)
        youtube_url = publish_youtube(archived, script, dry_run=False)

        m["published"] = True
        m["video"] = rel(archived)
        m["youtube_url"] = youtube_url
        ok += 1

    summary_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n完成：成功发布 {ok}/{len(made)}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
