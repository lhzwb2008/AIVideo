#!/usr/bin/env python3
"""批量发布 output/ 下尚未发布的 MP4 到抖音（与制作流程解耦，需手动执行）。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from paths import ROOT

VIDEO_TZ = ZoneInfo("Asia/Shanghai")
SCRIPT_MATCH_WINDOW = timedelta(minutes=45)
PUBLISHED_LOG = ROOT / "logs" / "published_videos.json"
MANIFEST = ROOT / "logs" / "video_manifest.jsonl"
BATCH_PROGRESS = ROOT / "logs" / "batch_progress.json"


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        import os

        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def norm_video(path: str) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    return str(p.resolve().relative_to(ROOT.resolve()))


def load_published() -> set[str]:
    if not PUBLISHED_LOG.is_file():
        return set()
    data = json.loads(PUBLISHED_LOG.read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else data.get("videos", [])
    return {norm_video(v) for v in items}


def save_published(videos: set[str]) -> None:
    PUBLISHED_LOG.parent.mkdir(parents=True, exist_ok=True)
    PUBLISHED_LOG.write_text(
        json.dumps(
            {"videos": sorted(videos), "updated_at": datetime.now(timezone.utc).isoformat()},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _parse_iso_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def video_created_at(video: Path) -> datetime | None:
    m = re.match(r"(\d{8})_(\d{6})", video.stem)
    if not m:
        return None
    local = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(tzinfo=VIDEO_TZ)
    return local.astimezone(timezone.utc)


def collect_script_candidates() -> list[tuple[Path, datetime]]:
    candidates: list[tuple[Path, datetime]] = []
    paths: list[Path] = []
    batch_dir = ROOT / "logs" / "batch"
    if batch_dir.is_dir():
        paths.extend(sorted(batch_dir.glob("*_script.json")))
    last = ROOT / "logs" / "last_script.json"
    if last.is_file():
        paths.append(last)
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        created = _parse_iso_dt(str(data.get("created_at") or ""))
        if created is None:
            created = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        candidates.append((path, created))
    return sorted(candidates, key=lambda item: item[1])


def match_scripts_by_time(videos: list[Path], mapping: dict[str, str]) -> None:
    """为尚无映射的视频，按文件名时间与脚本 created_at 就近匹配（全局贪心）。"""
    used_scripts = {Path(v).resolve() for v in mapping.values() if (ROOT / v).is_file()}
    scripts = collect_script_candidates()
    pairs: list[tuple[timedelta, Path, Path]] = []

    for video in videos:
        key = norm_video(str(video.relative_to(ROOT)))
        if key in mapping:
            continue
        vdt = video_created_at(video)
        if not vdt:
            continue
        for script_path, sdt in scripts:
            delta = abs(vdt - sdt)
            if delta <= SCRIPT_MATCH_WINDOW:
                pairs.append((delta, video, script_path))

    pairs.sort(key=lambda item: item[0])
    for _, video, script_path in pairs:
        key = norm_video(str(video.relative_to(ROOT)))
        resolved = script_path.resolve()
        if key in mapping or resolved in used_scripts:
            continue
        mapping[key] = str(script_path.relative_to(ROOT))
        used_scripts.add(resolved)


def build_video_script_map(videos: list[Path] | None = None) -> dict[str, str]:
    mapping: dict[str, str] = {}

    if MANIFEST.is_file():
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            video = row.get("video")
            script = row.get("script")
            if video and script:
                mapping[norm_video(str(video))] = str(script)

    if BATCH_PROGRESS.is_file():
        data = json.loads(BATCH_PROGRESS.read_text(encoding="utf-8"))
        for item in data.get("completed") or []:
            video = item.get("video")
            script = item.get("script")
            if video and script:
                mapping[norm_video(str(video))] = str(script)

    last_video = ROOT / "logs" / "last_video.txt"
    last_script = ROOT / "logs" / "last_script.json"
    if last_video.is_file() and last_script.is_file():
        video_key = norm_video(last_video.read_text(encoding="utf-8").strip())
        vpath = ROOT / video_key
        if vpath.is_file():
            try:
                meta = json.loads(last_script.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                meta = {}
            sdt = _parse_iso_dt(str(meta.get("created_at") or ""))
            vdt = video_created_at(vpath)
            if vdt and sdt and abs(vdt - sdt) <= SCRIPT_MATCH_WINDOW:
                mapping.setdefault(video_key, str(last_script.relative_to(ROOT)))

    if videos:
        match_scripts_by_time(videos, mapping)

    return mapping


def list_videos(output_dir: Path) -> list[Path]:
    return sorted(output_dir.glob("*.mp4"), key=lambda p: p.name)


def publish_one(video: Path, script: Path | None, extra_args: list[str]) -> int:
    rel_video = norm_video(str(video.relative_to(ROOT)))
    cmd = [str(ROOT / "publish-douyin.sh"), rel_video]
    if script and script.is_file():
        cmd.extend(["--script", str(script.relative_to(ROOT))])
    cmd.extend(extra_args)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src") + (
        f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else ""
    )
    print(f"\n>>> 发布 {video.name}", flush=True)
    return subprocess.run(cmd, cwd=ROOT, env=env).returncode


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="批量发布 output/ 下 MP4 到抖音")
    parser.add_argument("--output-dir", default=str(ROOT / "output"), help="视频目录，默认 output/")
    parser.add_argument("--force", action="store_true", help="包含已发布过的视频")
    parser.add_argument("--dry-run", action="store_true", help="只列出待发布列表")
    parser.add_argument("--assist", action="store_true", help="半自动：填表后手动点发布")
    parser.add_argument("--headed", action="store_true", help="有头 Chrome")
    parser.add_argument("--check", action="store_true", help="每条发布前校验 cookie")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if not output_dir.is_dir():
        print(f"目录不存在: {output_dir}", file=sys.stderr)
        return 1

    videos = list_videos(output_dir)
    if not videos:
        print("output/ 下没有 mp4")
        return 0

    published = set() if args.force else load_published()
    script_map = build_video_script_map(videos)

    pending: list[tuple[Path, Path | None]] = []
    for video in videos:
        key = norm_video(str(video.relative_to(ROOT)))
        if key in published:
            continue
        script_path = script_map.get(key)
        script = ROOT / script_path if script_path else None
        pending.append((video, script))

    if not pending:
        print(f"共 {len(videos)} 个视频，均已发布。用 --force 可重发。")
        return 0

    print(f"待发布 {len(pending)}/{len(videos)} 个视频")
    for video, script in pending:
        script_hint = script.relative_to(ROOT) if script else "(无脚本，需 --title 或手动指定 --script)"
        print(f"  - {video.name}  ←  {script_hint}")

    if args.dry_run:
        return 0

    extra: list[str] = []
    if args.assist:
        extra.append("--assist")
    if args.headed:
        extra.append("--headed")
    if args.check:
        extra.append("--check")

    ok = 0
    failed = 0
    for video, script in pending:
        rc = publish_one(video, script, extra)
        key = norm_video(str(video.relative_to(ROOT)))
        if rc == 0:
            ok += 1
            published.add(key)
            save_published(published)
        else:
            failed += 1
            print(f"  ✗ 失败，继续下一条…", flush=True)

    print(f"\n完成：成功 {ok}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
