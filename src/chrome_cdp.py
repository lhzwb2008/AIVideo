"""常驻真实 Chrome（CDP）保活：检测 / 启动 / 重启。

供 make-and-publish.ps1 与 llm_browser_publish.py 共用。
Chrome 用 --remote-debugging-port 独立启动（非 Playwright launch），
避免自动化指纹；进程挂掉后可自动拉起同一 user-data-dir，登录态一般仍在。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from paths import ROOT
from sau_paths import chrome_executable


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def cdp_enabled() -> bool:
    """是否启用 CDP 常驻 Chrome（默认开；设 0 关闭）。"""
    raw = _env("AIVIDEO_USE_CHROME_CDP", "1").lower()
    return raw not in ("0", "false", "no", "off")


def cdp_port() -> int:
    raw = _env("AIVIDEO_CHROME_CDP_PORT", "9222")
    try:
        return max(1, int(raw))
    except ValueError:
        return 9222


def cdp_url(port: int | None = None) -> str:
    configured = _env("AIVIDEO_CHROME_CDP_URL")
    if configured and port is None:
        return configured
    p = port if port is not None else cdp_port()
    return f"http://127.0.0.1:{p}"


def cdp_profile_dir() -> Path:
    custom = _env("AIVIDEO_CHROME_CDP_PROFILE")
    if custom:
        return Path(custom).expanduser().resolve()
    return (ROOT / "chrome-cdp-profile").resolve()


def is_cdp_alive(url: str | None = None, *, timeout_s: float = 2.0) -> bool:
    endpoint = (url or cdp_url()).rstrip("/") + "/json/version"
    try:
        with urllib.request.urlopen(endpoint, timeout=timeout_s) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def start_cdp_chrome(*, port: int | None = None, profile: Path | None = None) -> str:
    """启动常驻 CDP Chrome，返回 CDP URL。已存活则直接返回。"""
    port = port if port is not None else cdp_port()
    url = cdp_url(port)
    if is_cdp_alive(url):
        return url

    chrome = chrome_executable()
    if not chrome:
        raise RuntimeError(
            "未找到 Google Chrome。请安装 Chrome 或设置 LOCAL_CHROME_PATH。"
        )

    profile = profile or cdp_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)

    args = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--lang=zh-CN",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
    ]
    print(
        f"  [cdp] 启动常驻 Chrome (port {port}, profile {profile})",
        flush=True,
    )
    popen_kw: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # 脱离父进程控制台，避免计划任务结束时连带关掉 Chrome。
        # 不用 CREATE_NO_WINDOW：Chrome 需要可见窗口供扫码登录。
        popen_kw["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
        popen_kw["close_fds"] = True
    else:
        popen_kw["start_new_session"] = True

    subprocess.Popen(args, **popen_kw)

    deadline = time.time() + 20
    while time.time() < deadline:
        if is_cdp_alive(url):
            os.environ["AIVIDEO_CHROME_CDP_URL"] = url
            return url
        time.sleep(0.5)

    raise RuntimeError(f"CDP Chrome 启动超时（{url} 无响应）")


def ensure_cdp_chrome(*, restart_if_dead: bool = True) -> str | None:
    """确保 CDP Chrome 可用。

    - AIVIDEO_USE_CHROME_CDP=0 且未显式设置 URL → 返回 None（走旧 launch）
    - 已存活 → 返回 URL
    - 已死且 restart_if_dead → 重启后返回 URL
    """
    url = _env("AIVIDEO_CHROME_CDP_URL") or (cdp_url() if cdp_enabled() else "")
    if not url and not cdp_enabled():
        return None

    url = url or cdp_url()
    if is_cdp_alive(url):
        os.environ["AIVIDEO_CHROME_CDP_URL"] = url
        return url

    if not restart_if_dead and not cdp_enabled():
        return None

    print(f"  [cdp] {url} 无响应，尝试重新拉起…", flush=True)
    return start_cdp_chrome()


def reconnect_cdp_chrome() -> str:
    """强制按当前端口/profile 重新拉起（用于 TargetClosed / ECONNREFUSED）。"""
    url = cdp_url()
    if is_cdp_alive(url):
        # 端口还活着但连接异常：稍等再测一次
        time.sleep(1.0)
        if is_cdp_alive(url):
            os.environ["AIVIDEO_CHROME_CDP_URL"] = url
            return url
    return start_cdp_chrome()
