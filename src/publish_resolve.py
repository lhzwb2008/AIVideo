"""发布前解析：按视频找脚本、封面（与平台无关）。"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from paths import ROOT


def load_script(path: Path | None) -> dict | None:
    if not path or not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("script", data)


def resolve_script_for_video(video_path: Path, script_arg: str | None) -> Path | None:
    """为 MP4 找对应的 logs/last_script_*.json。"""
    if script_arg:
        path = Path(script_arg)
        if not path.is_absolute():
            path = ROOT / path
        return path if path.is_file() else None

    video_path = video_path.resolve()

    last_video = ROOT / "logs" / "last_video.txt"
    last_script = ROOT / "logs" / "last_script.json"
    if last_video.is_file() and last_script.is_file():
        raw = last_video.read_text(encoding="utf-8").strip()
        last = Path(raw)
        if not last.is_absolute():
            last = ROOT / last
        if last.resolve() == video_path:
            return last_script

    manifest = ROOT / "logs" / "video_manifest.jsonl"
    if manifest.is_file():
        rel = str(video_path.relative_to(ROOT.resolve())) if video_path.is_relative_to(ROOT.resolve()) else str(video_path)
        name = video_path.name
        for line in reversed(manifest.read_text(encoding="utf-8").splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            v = str(row.get("video") or "")
            if v.endswith(name) or v == rel or v == str(video_path):
                sp = row.get("script")
                if sp:
                    p = Path(sp)
                    if not p.is_absolute():
                        p = ROOT / p
                    if p.is_file():
                        return p

    def _dt_from_stamp(stamp: str) -> datetime | None:
        try:
            return datetime.strptime(stamp, "%Y%m%d%H%M%S")
        except ValueError:
            return None

    vm = re.match(r"^(\d{8})_(\d{6})\.mp4$", video_path.name)
    video_dt = _dt_from_stamp(vm.group(1) + vm.group(2)) if vm else None

    best_path: Path | None = None
    best_delta: float | None = None
    for p in ROOT.glob("logs/last_script_*.json"):
        sm = re.search(r"last_script_(\d{8})_(\d{6})", p.stem)
        if video_dt and sm:
            script_dt = _dt_from_stamp(sm.group(1) + sm.group(2))
            if not script_dt or script_dt > video_dt:
                continue
            delta = (video_dt - script_dt).total_seconds()
            if delta > 6 * 3600:
                continue
        else:
            delta = abs(p.stat().st_mtime - video_path.stat().st_mtime)
            if delta > 6 * 3600:
                continue
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_path = p

    if best_path:
        return best_path

    # 合成时间：脚本 mtime 略早于视频
    v_mtime = video_path.stat().st_mtime
    by_mtime: Path | None = None
    by_delta: float | None = None
    for p in ROOT.glob("logs/last_script_*.json"):
        sm = p.stat().st_mtime
        if sm > v_mtime:
            continue
        delta = v_mtime - sm
        if delta > 6 * 3600:
            continue
        if by_delta is None or delta < by_delta:
            by_delta = delta
            by_mtime = p
    return by_mtime


def resolve_cover_image(script_path: Path | None, video_path: Path) -> Path | None:
    """封面图：脚本 cover.png / cover_image → 视频首帧（首页）。"""
    if script_path and script_path.is_file():
        script = load_script(script_path)
        if script:
            rel = script.get("cover_image")
            if rel:
                p = Path(rel)
                if not p.is_absolute():
                    p = ROOT / p
                if p.is_file():
                    return p
        img_dir = ROOT / "logs" / "images" / script_path.stem
        for name in ("cover.png", "slide_00.png", "slide_01.png"):
            p = img_dir / name
            if p.is_file():
                return p

    return extract_first_frame(video_path)


def extract_first_frame(video_path: Path) -> Path | None:
    """从成片提取第 0 秒帧（即首页封面段首帧）。"""
    if not video_path.is_file():
        return None
    out_dir = ROOT / "logs" / "youtube_thumbs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{video_path.stem}_frame0.jpg"
    cmd = [
        "ffmpeg", "-y",
        "-ss", "0",
        "-i", str(video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(out),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not out.is_file():
        return None
    return out
