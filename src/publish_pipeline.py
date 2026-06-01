#!/usr/bin/env python3
"""制作发布共用流水线：脚本落地 → 生图 → 合成 → YouTube/TikTok API → 打印文案 → 归档。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from paths import ROOT
from publish_caption import (
    print_manual_publish_pack,
    tiktok_enabled,
    youtube_enabled,
)
from research import run_article_research


def log(message: str) -> None:
    print(message, flush=True)


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve()))


def run(cmd: list[str], *, label: str) -> None:
    log(f"\n[{label}] {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT, env=os.environ.copy())
    if proc.returncode != 0:
        raise RuntimeError(f"{label} 失败，退出码 {proc.returncode}")


def read_script_title(script_path: Path) -> str:
    try:
        data = json.loads(script_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    script = data.get("script") or data
    return str(script.get("title") or "").strip()


def latest_video() -> Path:
    last_video = ROOT / "logs" / "last_video.txt"
    if not last_video.is_file():
        raise RuntimeError("未找到 logs/last_video.txt")
    raw = last_video.read_text(encoding="utf-8").strip()
    video = Path(raw)
    if not video.is_absolute():
        video = ROOT / video
    if not video.is_file():
        raise RuntimeError(f"视频文件不存在: {video}")
    return video


def _read_last_publish_url(log_name: str, *keys: str) -> str:
    log_path = ROOT / "logs" / log_name
    if not log_path.is_file():
        return ""
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    for key in keys:
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def publish_youtube_api(video: Path, script_path: Path, *, dry_run: bool) -> str:
    cmd = [
        str(ROOT / "scripts" / "publish-youtube.sh"),
        rel(video),
        "--script",
        rel(script_path),
    ]
    if dry_run:
        cmd.append("--dry-run")
    run(cmd, label="发布YouTube")
    if dry_run:
        return ""
    return _read_last_publish_url("last_youtube_publish.json", "shorts_url", "url")


def publish_tiktok_api(video: Path, script_path: Path, *, dry_run: bool) -> str:
    cmd = [
        str(ROOT / "scripts" / "publish-tiktok.sh"),
        rel(video),
        "--script",
        rel(script_path),
    ]
    if dry_run:
        cmd.append("--dry-run")
    run(cmd, label="发布TikTok")
    if dry_run:
        return ""
    return _read_last_publish_url("last_tiktok_publish.json", "url")


def publish_youtube(video: Path, script_path: Path, *, dry_run: bool) -> str:
    if not youtube_enabled():
        return ""
    try:
        url = publish_youtube_api(video, script_path, dry_run=dry_run)
        if url:
            log(f"  [YouTube] {url}")
        return url
    except Exception as exc:  # noqa: BLE001
        log(f"  ⚠️ [YouTube] 发布失败（不影响成片/手动发布）：{exc}")
        return ""


def publish_tiktok(video: Path, script_path: Path, *, dry_run: bool) -> str:
    if not tiktok_enabled():
        return ""
    try:
        url = publish_tiktok_api(video, script_path, dry_run=dry_run)
        if url:
            log(f"  [TikTok] {url}")
        elif not dry_run:
            log("  [TikTok] 已上传到收件箱，请在 App 内粘贴终端文案后发布")
        return url
    except Exception as exc:  # noqa: BLE001
        log(f"  ⚠️ [TikTok] 发布失败（不影响成片/手动发布）：{exc}")
        return ""


def archive_video(video: Path, *, date_tag: str) -> Path:
    """仅归档 mp4（兼容旧调用）；主流程请用 archive_publish_bundle。"""
    return archive_publish_bundle(video, date_tag=date_tag)["video"]


def archive_publish_bundle(video: Path, *, date_tag: str) -> dict[str, Path | None]:
    """归档 mp4 + 同名图文文件夹到 archive/published/YYYYMMDD/。"""
    from forum_manual_pack import forum_dir_for_video

    dest_dir = ROOT / "archive" / "published" / date_tag
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = video.stem

    video_target = dest_dir / video.name
    if video_target.exists():
        video_target = dest_dir / f"{stem}_{datetime.now().strftime('%H%M%S')}{video.suffix}"
    shutil.move(str(video), str(video_target))

    forum_target: Path | None = None
    forum_src = forum_dir_for_video(video)
    if forum_src.is_dir() and forum_src.resolve() != dest_dir.resolve():
        forum_target = dest_dir / stem
        if forum_target.exists():
            forum_target = dest_dir / f"{stem}_forum_{datetime.now().strftime('%H%M%S')}"
        shutil.move(str(forum_src), str(forum_target))

    return {"video": video_target, "forum": forum_target}


def generate_forum_pack(script_path: Path, video: Path) -> Path | None:
    if os.environ.get("AIVIDEO_FORUM_POST", "1").strip().lower() in ("0", "false", "no"):
        return None
    try:
        from forum_manual_pack import build_forum_pack, forum_dir_for_video

        forum_dir = forum_dir_for_video(video)
        build_forum_pack(script_path, video, forum_dir)
        log(f"论坛图文：{rel(forum_dir)}/（post.md + cover.jpg + cover_landscape.jpg + images/）")
        return forum_dir
    except Exception as exc:  # noqa: BLE001
        log(f"论坛图文生成跳过: {exc}")
        return None


def pipeline_after_script(
    script_path: Path,
    title: str,
    *,
    index: int,
    target: int,
    publish_check: bool,
    dry_run: bool,
    append_history_fn,
    skip_publish: bool = False,
) -> dict:
    del publish_check  # 保留参数兼容；国内平台改手动发布

    run([str(ROOT / "scripts" / "run-enrich-images.sh"), str(script_path)], label="生图")
    run([str(ROOT / "scripts" / "run-compose.sh"), str(script_path)], label="合成")
    video = latest_video()
    generate_forum_pack(script_path, video)

    if skip_publish:
        log(f"\n=== [{index}/{target}] 跳过自动发布（--no-publish）===")
        print_manual_publish_pack(script_path, video, skip_auto_note=True)
        return {"title": title, "video": rel(video), "script": rel(script_path), "published": False}

    youtube_url = ""
    tiktok_url = ""

    if dry_run:
        log(f"\n=== [{index}/{target}] 预演 API 发布 ===")
        youtube_url = publish_youtube(video, script_path, dry_run=True)
        tiktok_url = publish_tiktok(video, script_path, dry_run=True)
        print_manual_publish_pack(
            script_path,
            video,
            youtube_url=youtube_url,
            tiktok_url=tiktok_url,
            skip_auto_note=True,
        )
        return {
            "title": title,
            "video": rel(video),
            "script": rel(script_path),
            "published": False,
            "youtube_url": youtube_url,
            "tiktok_url": tiktok_url,
        }

    if youtube_enabled() or tiktok_enabled():
        log(f"\n=== [{index}/{target}] API 自动发布（YouTube / TikTok）===")
    youtube_url = publish_youtube(video, script_path, dry_run=False)
    tiktok_url = publish_tiktok(video, script_path, dry_run=False)

    log(f"\n=== [{index}/{target}] 手动发布文案 ===")
    print_manual_publish_pack(
        script_path,
        video,
        youtube_url=youtube_url,
        tiktok_url=tiktok_url,
    )

    append_history_fn(script_path)
    date_tag = datetime.now().strftime("%Y%m%d")
    archived = archive_publish_bundle(video, date_tag=date_tag)
    log(f"已归档：{rel(archived['video'])}")
    if archived.get("forum"):
        log(f"  论坛图文：{rel(archived['forum'])}/")

    return {
        "title": title,
        "video": rel(archived["video"]),
        "forum": rel(archived["forum"]) if archived.get("forum") else "",
        "script": rel(script_path),
        "published": bool(youtube_url or tiktok_url),
        "youtube_url": youtube_url,
        "tiktok_url": tiktok_url,
    }


def process_topic(
    index: int,
    *,
    target: int,
    topic: dict,
    article: dict,
    details: dict,
    publish_check: bool,
    dry_run: bool,
    append_history_fn,
    skip_publish: bool = False,
) -> dict:
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    script_path = logs_dir / f"last_script_{stamp}_topic{index:02d}.json"

    log(f"\n=== [{index}/{target}] 话题：{topic.get('title_hint')} ===")
    script, _ = run_article_research(
        output=script_path,
        auto_pick=True,
        source="exa",
        preselected_article=article,
        preselected_details=details,
        category=topic.get("category"),
    )
    title = str(script.get("title") or read_script_title(script_path) or "").strip()
    log(f"脚本标题：{title}")

    return pipeline_after_script(
        script_path,
        title,
        index=index,
        target=target,
        publish_check=publish_check,
        dry_run=dry_run,
        skip_publish=skip_publish,
        append_history_fn=append_history_fn,
    )
