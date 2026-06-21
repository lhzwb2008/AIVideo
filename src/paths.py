"""项目根目录（src 的上一级）。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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

