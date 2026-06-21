#!/usr/bin/env python3
"""LLM 视觉 + Playwright 国内短视频发布（抖音 / 视频号 / 小红书 / B站 / 知乎专栏）。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from invoke_script import script_argv
from paths import ROOT, resolve_video_for_publish

LLM_PLATFORMS = {
    "douyin": "抖音",
    "shipinhao": "视频号",
    "xiaohongshu": "小红书",
    "bilibili": "B站",
    "zhihu": "知乎专栏",
}

LOG_NAMES = {
    "douyin": "last_llm_douyin_publish.json",
    "shipinhao": "last_llm_shipinhao_publish.json",
    "xiaohongshu": "last_llm_xiaohongshu_publish.json",
    "bilibili": "last_llm_bilibili_publish.json",
    "zhihu": "last_llm_zhihu_publish.json",
}

FORUM_PLATFORMS = frozenset({"zhihu"})


def llm_browser_default() -> bool:
    raw = os.environ.get("AIVIDEO_PUBLISH_LLM_BROWSER", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _read_llm_publish_title(platform: str, *, video: Path) -> str:
    import json

    from locale_env import locale_logs_dir

    stem = video.stem
    for base in (locale_logs_dir(), ROOT / "logs"):
        log_path = base / LOG_NAMES[platform]
        if not log_path.is_file():
            continue
        try:
            data = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not data.get("ok"):
            raise RuntimeError(
                f"{LLM_PLATFORMS[platform]} LLM 发布未确认成功（见 {log_path}）"
            )
        logged = str(data.get("video") or "").strip()
        if logged:
            logged_stem = Path(logged).stem
            if logged_stem and logged_stem != stem:
                raise RuntimeError(
                    f"{LLM_PLATFORMS[platform]} 发布记录与当前视频不一致"
                    f"（日志 {logged_stem} ≠ {stem}，见 {log_path}）"
                )
        title = str(data.get("title") or "").strip()
        if title:
            return title
    return stem


def publish_llm_browser(
    platform: str,
    video: Path,
    script_path: Path | None = None,
    *,
    archive_dir: Path | None = None,
    dry_run: bool = False,
) -> str:
    """调用 scripts/publish-llm-browser.sh；成功返回标题。"""
    if platform not in LLM_PLATFORMS:
        raise ValueError(f"未知 LLM 平台: {platform}")
    video = resolve_video_for_publish(video)

    cmd = script_argv(
        "publish-llm-browser",
        platform,
        str(video),
    )
    if script_path and script_path.is_file():
        cmd.extend(["--script", str(script_path.resolve())])
    if archive_dir and archive_dir.is_dir():
        cmd.extend(["--archive-dir", str(archive_dir.resolve())])
    elif (video.parent / video.stem).is_dir():
        cmd.extend(["--archive-dir", str((video.parent / video.stem).resolve())])
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd.append("--confirm")

    label = LLM_PLATFORMS[platform]
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    proc = subprocess.run(cmd, cwd=ROOT, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"{label} LLM 发布失败，退出码 {proc.returncode}")
    if dry_run:
        return ""

    return _read_llm_publish_title(platform, video=video)


def publish_llm_browser_forum(
    platform: str,
    forum_dir: Path,
    *,
    dry_run: bool = False,
) -> str:
    """论坛图文 LLM 发布（知乎专栏）。"""
    if platform not in FORUM_PLATFORMS:
        raise ValueError(f"非论坛 LLM 平台: {platform}")
    forum_dir = forum_dir.resolve()
    if not (forum_dir / "post.md").is_file():
        raise FileNotFoundError(f"论坛包不存在: {forum_dir}")

    cmd = script_argv(
        "publish-llm-browser",
        platform,
        "--forum-dir",
        str(forum_dir),
    )
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd.append("--confirm")

    label = LLM_PLATFORMS[platform]
    proc = subprocess.run(cmd, cwd=ROOT, env=os.environ.copy())
    if proc.returncode != 0:
        raise RuntimeError(f"{label} LLM 发布失败，退出码 {proc.returncode}")
    if dry_run:
        return ""

    import json
    from locale_env import locale_logs_dir

    title = ""
    for base in (locale_logs_dir(), ROOT / "logs"):
        for name in (LOG_NAMES[platform], f"last_{platform}_publish.json"):
            log_path = base / name
            if not log_path.is_file():
                continue
            try:
                data = json.loads(log_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if name.startswith("last_llm") and not data.get("ok"):
                raise RuntimeError(
                    f"{label} LLM 发布未确认成功（见 {log_path}）"
                )
            title = str(data.get("title") or "").strip()
            if title:
                return title
    return forum_dir.name
