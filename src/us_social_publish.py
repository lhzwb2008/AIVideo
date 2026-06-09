#!/usr/bin/env python3
"""US 英文站：Instagram / Facebook Reels / LinkedIn 浏览器发布（供主流程调用）。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from paths import ROOT

SOCIAL_PLATFORMS = ("instagram", "facebook", "linkedin")

PLATFORM_LABEL = {
    "instagram": "Instagram Reels",
    "facebook": "Facebook Reels",
    "linkedin": "LinkedIn",
}


def _social_python() -> str:
    sau = ROOT / "vendor" / "social-auto-upload" / ".venv" / "bin" / "python3"
    if sau.is_file():
        return str(sau)
    py = ROOT / ".venv" / "bin" / "python3"
    if py.is_file():
        return str(py)
    return sys.executable


def publish_us_social(
    platform: str,
    video: Path,
    script_path: Path,
    *,
    dry_run: bool = False,
    headless: bool = True,
) -> str:
    """调用 scripts/test_us_social_publish.py 发布；成功返回平台名，失败抛异常。"""
    if platform not in SOCIAL_PLATFORMS:
        raise ValueError(f"未知平台: {platform}")
    if dry_run:
        return f"{platform}:dry-run"
    cmd = [
        _social_python(),
        str(ROOT / "scripts" / "test_us_social_publish.py"),
        "publish",
        platform,
        "--video",
        str(video),
        "--script",
        str(script_path),
    ]
    if headless:
        cmd.append("--headless")
    env = os.environ.copy()
    env["ROOT"] = str(ROOT)
    env["PYTHONPATH"] = str(ROOT / "src") + (
        f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else ""
    )
    proc = subprocess.run(cmd, cwd=str(ROOT), env=env, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{PLATFORM_LABEL[platform]} 发布失败 (exit {proc.returncode})")
    return platform


def publish_all_us_social(
    video: Path,
    script_path: Path,
    *,
    dry_run: bool = False,
    headless: bool = True,
    platforms: tuple[str, ...] | None = None,
) -> dict[str, str]:
    from publish_caption import facebook_enabled, instagram_enabled, linkedin_enabled

    enabled: list[str] = []
    if instagram_enabled():
        enabled.append("instagram")
    if facebook_enabled():
        enabled.append("facebook")
    if linkedin_enabled():
        enabled.append("linkedin")
    if platforms:
        enabled = [p for p in platforms if p in enabled]

    results: dict[str, str] = {}
    errors: list[str] = []
    for plat in enabled:
        try:
            results[plat] = publish_us_social(
                plat, video, script_path, dry_run=dry_run, headless=headless
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{plat}: {exc}")
    if errors and not results:
        raise RuntimeError("; ".join(errors))
    if errors:
        results["_errors"] = "; ".join(errors)
    return results
