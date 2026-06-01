#!/usr/bin/env python3
"""制作发布共用流水线：脚本落地 → 生图 → 合成 → 抖音 → 归档 → YouTube / 其它平台。"""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from paths import ROOT
from publish_all_douyin import load_published, save_published
from research import run_article_research


def youtube_enabled() -> bool:
    value = os.environ.get("AIVIDEO_PUBLISH_YOUTUBE")
    if value is None or value.strip() == "":
        return True
    return value.strip().lower() in ("1", "true", "yes", "on")

SOCIAL_PLATFORMS = {
    "xiaohongshu": ("AIVIDEO_PUBLISH_XHS", True),
    "kuaishou": ("AIVIDEO_PUBLISH_KS", False),
    "shipinhao": ("AIVIDEO_PUBLISH_SHIPINHAO", False),
}
SOCIAL_LABEL = {"xiaohongshu": "小红书", "kuaishou": "快手", "shipinhao": "视频号"}


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


def _social_enabled(platform: str) -> bool:
    env_key, default = SOCIAL_PLATFORMS[platform]
    value = os.environ.get(env_key)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _social_gap_seconds() -> int:
    try:
        lo = int(os.environ.get("AIVIDEO_SOCIAL_GAP_MIN", "45"))
        hi = int(os.environ.get("AIVIDEO_SOCIAL_GAP_MAX", "120"))
    except ValueError:
        lo, hi = 45, 120
    lo = max(0, lo)
    hi = max(lo, hi)
    return random.randint(lo, hi)


def _read_last_youtube_url() -> str:
    log_path = ROOT / "logs" / "last_youtube_publish.json"
    if not log_path.is_file():
        return ""
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(data.get("shorts_url") or data.get("url") or "").strip()


def publish_youtube_api(video: Path, script_path: Path, *, dry_run: bool) -> str:
    """YouTube Data API 发布（best-effort）。返回发布 URL（若有）。"""
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
    return _read_last_youtube_url()


def publish_social(video: Path, script_path: Path) -> None:
    from backfill_social import load_platform_published, save_platform_published

    attempted = 0
    for platform in SOCIAL_PLATFORMS:
        if not _social_enabled(platform):
            continue
        label = SOCIAL_LABEL[platform]
        done = load_platform_published(platform)
        if video.name in done:
            log(f"  [{label}] 已发过，跳过")
            continue
        if attempted > 0:
            gap = _social_gap_seconds()
            log(f"  ⏳ 拟人化间隔 {gap}s 后再发{label}…")
            time.sleep(gap)
        attempted += 1
        try:
            cmd = [
                str(ROOT / "scripts" / "publish-social.sh"),
                platform,
                rel(video),
                "--script",
                rel(script_path),
            ]
            run(cmd, label=f"发布{label}")
            done.add(video.name)
            save_platform_published(platform, done)
            log(f"  [{label}] 发布成功")
        except Exception as exc:  # noqa: BLE001
            log(f"  ⚠️ [{label}] 发布失败（不影响抖音/主流程）：{exc}")


def publish_youtube(video: Path, script_path: Path, *, dry_run: bool) -> str:
    if not youtube_enabled():
        return ""
    try:
        url = publish_youtube_api(video, script_path, dry_run=dry_run)
        if url:
            log(f"  [YouTube] 发布成功: {url}")
        return url
    except Exception as exc:  # noqa: BLE001
        log(f"  ⚠️ [YouTube] 发布失败（不影响抖音/主流程）：{exc}")
        return ""


def archive_video(video: Path, *, date_tag: str) -> Path:
    dest_dir = ROOT / "archive" / "published" / date_tag
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / video.name
    if target.exists():
        target = dest_dir / f"{video.stem}_{datetime.now().strftime('%H%M%S')}{video.suffix}"
    shutil.move(str(video), str(target))
    return target


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
    run([str(ROOT / "scripts" / "run-enrich-images.sh"), str(script_path)], label="生图")
    run([str(ROOT / "scripts" / "run-compose.sh"), str(script_path)], label="合成")
    video = latest_video()

    if skip_publish:
        log(f"\n=== [{index}/{target}] 跳过发布（--no-publish）===")
        return {"title": title, "video": rel(video), "script": rel(script_path), "published": False}

    log(f"\n=== [{index}/{target}] 发布抖音 ===")
    publish_cmd = [str(ROOT / "scripts" / "publish-douyin.sh"), rel(video), "--script", rel(script_path)]
    if publish_check:
        publish_cmd.append("--check")
    if dry_run:
        publish_cmd.append("--dry-run")
    run(publish_cmd, label="发布")

    youtube_url = ""
    if dry_run:
        log(f"\n=== [{index}/{target}] 预演 YouTube ===")
        youtube_url = publish_youtube(video, script_path, dry_run=True)
        return {
            "title": title,
            "video": rel(video),
            "script": rel(script_path),
            "published": False,
            "youtube_url": youtube_url,
        }

    published = load_published()
    video_rel = rel(video)
    published.add(video_rel)
    save_published(published)
    append_history_fn(script_path)
    archived = archive_video(video, date_tag=datetime.now().strftime("%Y%m%d"))
    log(f"抖音发布成功，已归档：{rel(archived)}")

    log(f"\n=== [{index}/{target}] 联动发布其它平台 ===")
    publish_social(archived, script_path)
    youtube_url = publish_youtube(archived, script_path, dry_run=False)

    return {
        "title": title,
        "video": rel(archived),
        "script": rel(script_path),
        "published": True,
        "youtube_url": youtube_url,
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
