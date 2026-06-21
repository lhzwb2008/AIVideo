#!/usr/bin/env python3
"""LLM 视觉 + Playwright 国内短视频发布（抖音 / 视频号 / 小红书 / B站 / 知乎专栏）。"""

from __future__ import annotations

import os
import subprocess
import time
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

_PROFILE_LOCK_DIR = ROOT / "logs" / "locks"


class ProfilePublishLockError(RuntimeError):
    pass


def _acquire_profile_lock(platform: str, *, timeout_s: int = 900) -> Path:
    """同一平台 Chrome Profile 同时只允许一个发布子进程（防无限重开）。"""
    _PROFILE_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    lock_path = _PROFILE_LOCK_DIR / f"{platform}_publish.lock"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()}\n{time.time()}".encode())
            os.close(fd)
            return lock_path
        except FileExistsError:
            try:
                raw = lock_path.read_text(encoding="utf-8").strip().splitlines()
                owner = int(raw[0]) if raw else 0
                if owner and owner != os.getpid():
                    try:
                        os.kill(owner, 0)
                    except OSError:
                        lock_path.unlink(missing_ok=True)
                        continue
            except (OSError, ValueError):
                pass
            time.sleep(5)
    raise ProfilePublishLockError(
        f"{LLM_PLATFORMS.get(platform, platform)} 发布锁占用超时（{timeout_s}s），"
        f"请关闭该平台 Chrome 窗口后重试: {lock_path}"
    )


def _release_profile_lock(lock_path: Path | None) -> None:
    if not lock_path:
        return
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def llm_publish_log_paths(platform: str) -> list[Path]:
    from locale_env import locale_logs_dir

    name = LOG_NAMES[platform]
    primary = locale_logs_dir() / name
    legacy = ROOT / "logs" / name
    paths = [primary]
    if legacy.resolve() != primary.resolve():
        paths.append(legacy)
    return paths


def stamp_llm_publish_log(platform: str, video: Path, *, title: str = "") -> None:
    import json
    from datetime import datetime, timezone

    payload = {
        "ok": False,
        "video": str(video.resolve()),
        "title": title,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "method": "llm_browser",
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    for log_path in llm_publish_log_paths(platform):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(text, encoding="utf-8")


def write_llm_publish_log(platform: str, payload: dict) -> Path:
    import json

    text = json.dumps(payload, ensure_ascii=False, indent=2)
    paths = llm_publish_log_paths(platform)
    for log_path in paths:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(text, encoding="utf-8")
    return paths[0]


def llm_browser_default() -> bool:
    raw = os.environ.get("AIVIDEO_PUBLISH_LLM_BROWSER", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _read_llm_publish_title(platform: str, *, video: Path) -> str:
    import json

    stem = video.stem
    matched: dict | None = None
    for log_path in llm_publish_log_paths(platform):
        if not log_path.is_file():
            continue
        try:
            data = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not data.get("ok"):
            continue
        logged = str(data.get("video") or "").strip()
        if logged and Path(logged).stem != stem:
            continue
        matched = data
        break
    if not matched:
        raise RuntimeError(
            f"{LLM_PLATFORMS[platform]} LLM 发布未确认成功"
            f"（无与 {stem} 匹配的成功日志）"
        )
    title = str(matched.get("title") or "").strip()
    return title or stem


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
    stamp_llm_publish_log(platform, video, title="")

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
    lock_path: Path | None = None
    try:
        lock_path = _acquire_profile_lock(platform)
        proc = subprocess.run(cmd, cwd=ROOT, env=env)
    finally:
        _release_profile_lock(lock_path)
    if proc.returncode != 0:
        cooldown = int(os.environ.get("LLM_BROWSER_PROFILE_COOLDOWN", "30"))
        if cooldown > 0:
            time.sleep(cooldown)
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
