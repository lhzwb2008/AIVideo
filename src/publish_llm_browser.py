#!/usr/bin/env python3
"""LLM 视觉 + Playwright 国内短视频发布（抖音 / 视频号 / 小红书）。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from invoke_script import script_argv
from paths import ROOT

LLM_PLATFORMS = {
    "douyin": "抖音",
    "shipinhao": "视频号",
    "xiaohongshu": "小红书",
}

LOG_NAMES = {
    "douyin": "last_llm_douyin_publish.json",
    "shipinhao": "last_llm_shipinhao_publish.json",
    "xiaohongshu": "last_llm_xiaohongshu_publish.json",
}


def llm_browser_default() -> bool:
    raw = os.environ.get("AIVIDEO_PUBLISH_LLM_BROWSER", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


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
    video = video.resolve()
    if not video.is_file():
        raise FileNotFoundError(f"视频不存在: {video}")

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
    proc = subprocess.run(cmd, cwd=ROOT, env=os.environ.copy())
    if proc.returncode != 0:
        raise RuntimeError(f"{label} LLM 发布失败，退出码 {proc.returncode}")
    if dry_run:
        return ""

    import json
    from locale_env import locale_logs_dir

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
                f"{label} LLM 发布未确认成功（见 {log_path}）"
            )
        title = str(data.get("title") or "").strip()
        if title:
            return title
    return video.stem
