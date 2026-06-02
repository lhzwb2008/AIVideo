#!/usr/bin/env python3
"""校验雪球创作者中心登录态。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from paths import ROOT
from xueqiu_publisher import (
    EDITOR_URL,
    XueqiuPublishError,
    _chrome_path,
    _ensure_patchright,
    _open_longform_editor,
    cookie_path,
    profile_dir,
    sau_home,
)


async def login_interactive(*, account: str | None = None, timeout_s: float = 300) -> Path:
    """有头浏览器登录，保存 storage_state + 持久化 profile。"""
    _ensure_patchright()
    from patchright.async_api import async_playwright

    account = account or "main"
    cookie = sau_home(ROOT) / "cookies" / f"xueqiu_{account}.json"
    cookie.parent.mkdir(parents=True, exist_ok=True)
    profile = profile_dir(account=account)
    profile.mkdir(parents=True, exist_ok=True)
    chrome = _chrome_path()
    launch: dict = {
        "headless": False,
        "args": ["--disable-blink-features=AutomationControlled", "--lang=zh-CN"],
    }
    if chrome:
        launch["executable_path"] = chrome
    else:
        launch["channel"] = "chrome"

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 1000},
            **launch,
        )
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(
            "https://xueqiu.com/account/login?url=https://mp.xueqiu.com",
            wait_until="domcontentloaded",
            timeout=90_000,
        )
        print("请在浏览器中完成登录，进入长文编辑器后自动保存…", flush=True)
        for _ in range(int(timeout_s)):
            url = page.url.lower()
            if "login" not in url:
                try:
                    await _open_longform_editor(page)
                except Exception:
                    pass
                loc = page.locator('textarea[placeholder*="标题"], input[placeholder*="标题"], .ProseMirror')
                if await loc.count():
                    await context.storage_state(path=str(cookie))
                    await context.close()
                    return cookie
            await asyncio.sleep(1)
        await context.close()
    raise XueqiuPublishError("登录超时")


async def verify_editor(*, account: str | None = None) -> bool:
    _ensure_patchright()
    from patchright.async_api import async_playwright

    try:
        cookie = cookie_path(account=account)
    except XueqiuPublishError:
        return False
    launch: dict = {
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled", "--lang=zh-CN"],
    }
    chrome = _chrome_path()
    if chrome:
        launch["executable_path"] = chrome
    else:
        launch["channel"] = "chrome"

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch)
        try:
            context = await browser.new_context(
                storage_state=str(cookie),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                viewport={"width": 1440, "height": 900},
            )
            page = await context.new_page()
            await page.goto(EDITOR_URL, wait_until="domcontentloaded", timeout=90_000)
            from xueqiu_publisher import _editor_ready

            return await _editor_ready(page)
        finally:
            await browser.close()


def verify_editor_sync(*, account: str | None = None) -> bool:
    return asyncio.run(verify_editor(account=account))


def main() -> int:
    parser = argparse.ArgumentParser(description="校验雪球长文编辑器登录态")
    parser.add_argument("--account", default=None)
    parser.add_argument("--login", action="store_true", help="有头浏览器登录并保存 cookie")
    parser.add_argument("--check", action="store_true", help="校验登录态（默认行为）")
    args = parser.parse_args()
    try:
        if args.login:
            path = asyncio.run(login_interactive(account=args.account))
            print(f"已保存 cookie: {path}")
            return 0
        ok = asyncio.run(verify_editor(account=args.account))
    except XueqiuPublishError as exc:
        print(exc, file=sys.stderr)
        return 1
    if ok:
        print("雪球创作者中心：登录态有效")
        return 0
    print("雪球创作者中心：未登录或 cookie 已过期", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
