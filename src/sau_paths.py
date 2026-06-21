"""social-auto-upload venv 跨平台路径（Windows Scripts/Lib vs Unix bin/lib）。"""

from __future__ import annotations

import sys
from pathlib import Path


def sau_python(home: Path) -> Path | None:
    for candidate in (
        home / ".venv" / "Scripts" / "python.exe",
        home / ".venv" / "bin" / "python3",
        home / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return candidate
    return None


def sau_bin(home: Path) -> Path | None:
    """sau CLI（Windows: Scripts/sau.exe；Unix: bin/sau）。"""
    for candidate in (
        home / ".venv" / "Scripts" / "sau.exe",
        home / ".venv" / "Scripts" / "sau",
        home / ".venv" / "bin" / "sau",
        home / "venv" / "Scripts" / "sau.exe",
        home / "venv" / "bin" / "sau",
    ):
        if candidate.is_file():
            return candidate
    return None


def sau_site_packages(home: Path) -> Path | None:
    for lib in ("Lib", "lib"):
        site = home / ".venv" / lib / "site-packages"
        if site.is_dir():
            return site
    return None


def ensure_patchright_import(home: Path) -> None:
    site = sau_site_packages(home)
    if site:
        site_str = str(site)
        if site_str not in sys.path:
            sys.path.insert(0, site_str)
    from patchright.async_api import async_playwright  # noqa: F401


def chrome_executable() -> str:
    import os

    env = os.environ.get("LOCAL_CHROME_PATH", "").strip()
    candidates = [
        env,
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    return ""
