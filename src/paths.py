"""项目根目录（src 的上一级）。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def resolve_video_for_publish(video: Path) -> Path:
    """发布用视频路径：优先原路径，已归档则从 archive/published 查找同名 mp4。"""
    p = Path(video)
    if not p.is_absolute():
        p = (ROOT / p).resolve()
    else:
        p = p.resolve()
    if p.is_file():
        return p
    stem = p.stem
    candidates: list[Path] = []
    published = ROOT / "archive" / "published"
    if published.is_dir():
        for hit in published.glob(f"**/{stem}.mp4"):
            if hit.is_file():
                candidates.append(hit)
    if candidates:
        candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        return candidates[0].resolve()
    raise FileNotFoundError(f"视频不存在: {p}")


def ffmpeg_executable() -> str:
    """ffmpeg 可执行文件；Windows 可设 FFMPEG_PATH 或安装到 C:\\ffmpeg\\bin。"""
    custom = os.environ.get("FFMPEG_PATH", "").strip()
    if custom and Path(custom).is_file():
        return custom
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in (
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return "ffmpeg"

