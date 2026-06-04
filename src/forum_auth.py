"""东方财富 / 雪球论坛发布 · 登录态检测与交互式续期。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

LOGIN_MARKERS = (
    "未登录",
    "cookie",
    "login",
    "login.sh",
    "重新登录",
    "登录页",
    "usercenter",
    "account/login",
    "登录超时",
    "请先 ./",
    "未找到 cookie",
    "未进入长文编辑器",
    "未进入专栏编辑器",
    "wechat-login",
    "zhihu-login",
    "social-login.sh",
    "创作中心",
)


def is_login_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    # Playwright 选器超时多为页面改版，不应反复弹登录
    if "locator.wait_for" in text or ("timeout" in text and "exceeded" in text):
        return False
    if "can't be used in 'await' expression" in text or "TypeError" in text:
        return False
    if "人机验证" in text or "unhuman" in text or "captcha" in text:
        return True
    return any(marker.lower() in text for marker in LOGIN_MARKERS)


def _verify_sync(platform: str, *, account: str | None) -> bool:
    if platform == "eastmoney":
        from eastmoney_session import verify_editor_sync

        return verify_editor_sync(account=account)
    if platform == "xueqiu":
        from xueqiu_session import verify_editor_sync

        return verify_editor_sync(account=account)
    if platform == "zhihu":
        from zhihu_session import verify_editor_sync

        return verify_editor_sync(account=account)
    if platform == "xiaohongshu":
        from xhs_article_publisher import cookie_path

        try:
            path = cookie_path(account=account)
            return path.is_file() and path.stat().st_size > 64
        except Exception:
            return False
    raise ValueError(f"未知平台: {platform}")


def _login_sync(platform: str, *, account: str | None) -> None:
    if platform == "eastmoney":
        from eastmoney_session import login_interactive

        asyncio.run(login_interactive(account=account))
        return
    if platform == "xueqiu":
        from xueqiu_session import login_interactive

        asyncio.run(login_interactive(account=account))
        return
    if platform == "zhihu":
        from zhihu_session import login_interactive

        asyncio.run(login_interactive(account=account))
        return
    if platform == "xiaohongshu":
        import subprocess

        from paths import ROOT

        script = ROOT / "social-login.sh"
        if script.is_file():
            subprocess.run([str(script), "xiaohongshu"], cwd=str(ROOT), check=False)
        return
    raise ValueError(f"未知平台: {platform}")


def ensure_logged_in_sync(
    *,
    platform: str,
    account: str | None = None,
    label: str | None = None,
) -> None:
    """未登录或 cookie 失效时打开浏览器等待扫码，直到登录成功。"""
    label = label or {
        "eastmoney": "东方财富",
        "xueqiu": "雪球",
        "zhihu": "知乎专栏",
        "xiaohongshu": "小红书",
    }.get(platform, platform)
    if _verify_sync(platform, account=account):
        return

    print(f"\n🔐 [{label}] 需要登录，正在打开浏览器…", flush=True)
    print("   请在窗口内扫码/短信登录；完成后会自动继续发布，无需重启 make。", flush=True)

    while True:
        if _verify_sync(platform, account=account):
            print(f"✅ [{label}] 登录成功，继续发布…", flush=True)
            return
        try:
            _login_sync(platform, account=account)
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️ [{label}] 登录未完成：{exc}", flush=True)
            print(f"   请继续在浏览器中完成登录，程序会在此等待…", flush=True)
        if _verify_sync(platform, account=account):
            print(f"✅ [{label}] 登录成功，继续发布…", flush=True)
            return


def run_with_relogin(
    fn: Callable[[], T],
    *,
    platform: str,
    account: str | None = None,
    label: str | None = None,
    interactive_login: bool = True,
) -> T:
    """执行发布；遇登录失效则提示并等待扫码后自动重试，不退出进程。"""
    label = label or {
        "eastmoney": "东方财富",
        "xueqiu": "雪球",
        "zhihu": "知乎专栏",
        "xiaohongshu": "小红书",
    }.get(platform, platform)
    if interactive_login:
        ensure_logged_in_sync(platform=platform, account=account, label=label)

    while True:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if not interactive_login or not is_login_error(exc):
                raise
            print(f"\n🔐 [{label}] 登录态已失效：{exc}", flush=True)
            print("   即将打开浏览器，请重新登录；完成后会自动重试发布。", flush=True)
            ensure_logged_in_sync(platform=platform, account=account, label=label)
