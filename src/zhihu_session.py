#!/usr/bin/env python3
"""校验知乎专栏登录态。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from paths import ROOT
from zhihu_publisher import (
    WRITE_URL,
    ZhihuPublishError,
    _chrome_path,
    _editor_ready,
    _ensure_patchright,
    _open_new_write,
    cookie_path,
    profile_dir,
    sau_home,
)


async def login_interactive(*, account: str | None = None, timeout_s: float = 300) -> Path:
    _ensure_patchright()
    from patchright.async_api import async_playwright

    account = account or "main"
    cookie = sau_home(ROOT) / "cookies" / f"zhihu_{account}.json"
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
            "https://www.zhihu.com/signin?next=" + WRITE_URL,
            wait_until="domcontentloaded",
            timeout=90_000,
        )
        print("请在浏览器中完成登录，进入专栏写作页后自动保存…", flush=True)
        for _ in range(int(timeout_s)):
            try:
                await _open_new_write(page)
            except Exception:
                pass
            if await _editor_ready(page):
                await context.storage_state(path=str(cookie))
                await context.close()
                return cookie
            await asyncio.sleep(1)
        await context.close()
    raise ZhihuPublishError("登录超时")


async def verify_editor(*, account: str | None = None) -> bool:
    _ensure_patchright()
    from patchright.async_api import async_playwright

    launch: dict = {
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled", "--lang=zh-CN"],
    }
    chrome = _chrome_path()
    if chrome:
        launch["executable_path"] = chrome
    else:
        launch["channel"] = "chrome"

    try:
        cookie = cookie_path(account=account)
    except ZhihuPublishError:
        return False

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch)
        context = await browser.new_context(
            storage_state=str(cookie),
            locale="zh-CN",
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        try:
            await _open_new_write(page)
            return await _editor_ready(page)
        except Exception:
            return False
        finally:
            await context.close()


def verify_editor_sync(*, account: str | None = None) -> bool:
    return asyncio.run(verify_editor(account=account))


def main() -> int:
    parser = argparse.ArgumentParser(description="知乎专栏登录态")
    parser.add_argument("--account", default="main")
    parser.add_argument("--login", action="store_true", help="交互式登录")
    args = parser.parse_args()
    try:
        if args.login:
            path = asyncio.run(login_interactive(account=args.account))
            print(f"已保存: {path}")
            return 0
        ok = verify_editor_sync(account=args.account)
        print("登录有效" if ok else "未登录或 cookie 失效")
        return 0 if ok else 1
    except ZhihuPublishError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
