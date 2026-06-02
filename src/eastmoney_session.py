#!/usr/bin/env python3
"""校验东方财富创作平台登录态。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from eastmoney_publisher import (
    EDITOR_URL,
    EastmoneyPublishError,
    _chrome_path,
    _ensure_patchright,
    _open_longform_editor,
    cookie_path,
    profile_dir,
    sau_home,
)
from paths import ROOT


async def login_interactive(*, account: str | None = None, timeout_s: float = 300) -> Path:
    """有头浏览器登录，保存 storage_state + 持久化 profile。"""
    _ensure_patchright()
    from patchright.async_api import async_playwright

    account = account or "main"
    cookie = sau_home(ROOT) / "cookies" / f"eastmoney_{account}.json"
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
        await _open_longform_editor(page)
        print("请在浏览器中完成登录（扫码或短信），进入长文编辑器后自动保存…", flush=True)
        for _ in range(int(timeout_s)):
            url = page.url.lower()
            if "usercenter" not in url and "login" not in url:
                loc = page.locator('input[placeholder*="标题"]')
                if await loc.count():
                    await context.storage_state(path=str(cookie))
                    await context.close()
                    return cookie
            await asyncio.sleep(1)
        await context.close()
    raise EastmoneyPublishError("登录超时")


async def verify_editor(*, account: str | None = None) -> bool:
    _ensure_patchright()
    from patchright.async_api import async_playwright

    cookie = cookie_path(account=account)
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
            url = page.url.lower()
            if "usercenter" in url or "login" in url:
                return False
            loc = page.locator('input[placeholder*="标题"]')
            return await loc.count() > 0
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="校验东方财富长文编辑器登录态")
    parser.add_argument("--account", default=None)
    parser.add_argument("--login", action="store_true", help="有头浏览器登录并保存 cookie")
    args = parser.parse_args()
    try:
        if args.login:
            path = asyncio.run(login_interactive(account=args.account))
            print(f"已保存 cookie: {path}")
            return 0
        ok = asyncio.run(verify_editor(account=args.account))
    except EastmoneyPublishError as exc:
        print(exc, file=sys.stderr)
        return 1
    if ok:
        print("东方财富创作平台：登录态有效")
        return 0
    print("东方财富创作平台：未登录或 cookie 已过期", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
