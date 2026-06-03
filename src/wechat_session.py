#!/usr/bin/env python3
"""微信公众平台 · 登录态校验与扫码登录。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from paths import ROOT
from wechat_publisher import (
    WechatPublishError,
    _chrome_path,
    _draft_list_ready,
    _ensure_patchright,
    cookie_path,
    profile_dir,
    sau_home,
)

HOME_URL = "https://mp.weixin.qq.com/"


async def login_interactive(*, account: str | None = None, timeout_s: float = 300) -> Path:
    """有头浏览器扫码登录，保存 storage_state + 持久化 profile。"""
    _ensure_patchright()
    from patchright.async_api import async_playwright

    account = account or "main"
    cookie = sau_home(ROOT) / "cookies" / f"wechat_{account}.json"
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
        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=90_000)
        print("请用微信扫码登录公众平台，进入首页后自动保存登录态…", flush=True)
        for _ in range(int(timeout_s)):
            if await _draft_list_ready(page):
                await context.storage_state(path=str(cookie))
                await context.close()
                return cookie
            await asyncio.sleep(1)
        await context.close()
    raise WechatPublishError("登录超时（请确认已完成扫码并进入创作中心首页）")


async def verify_logged_in(*, account: str | None = None) -> bool:
    _ensure_patchright()
    from patchright.async_api import async_playwright

    account = account or "main"
    try:
        cookie = cookie_path(account=account)
    except WechatPublishError:
        return False
    if not cookie.is_file() or cookie.stat().st_size < 64:
        profile = profile_dir(account=account)
        if not profile.is_dir() or not any(profile.iterdir()):
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
        profile = profile_dir(account=account)
        if profile.is_dir() and any(profile.iterdir()):
            context = await p.chromium.launch_persistent_context(
                str(profile),
                locale="zh-CN",
                viewport={"width": 1440, "height": 900},
                **launch,
            )
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=90_000)
                return await _draft_list_ready(page)
            finally:
                await context.close()
        browser = await p.chromium.launch(**launch)
        try:
            context = await browser.new_context(
                storage_state=str(cookie),
                locale="zh-CN",
                viewport={"width": 1440, "height": 900},
            )
            page = await context.new_page()
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=90_000)
            return await _draft_list_ready(page)
        finally:
            await browser.close()


def verify_logged_in_sync(*, account: str | None = None) -> bool:
    return asyncio.run(verify_logged_in(account=account))


def main() -> int:
    parser = argparse.ArgumentParser(description="微信公众平台登录")
    parser.add_argument("--account", default=None)
    parser.add_argument("--login", action="store_true", help="有头浏览器扫码登录")
    parser.add_argument("--check", action="store_true", help="校验登录态")
    args = parser.parse_args()

    try:
        if args.login:
            path = asyncio.run(login_interactive(account=args.account))
            print(f"登录态已保存: {path}", flush=True)
            return 0
        ok = verify_logged_in_sync(account=args.account)
        account = args.account or "main"
        if ok:
            print(f"微信公众平台：登录态有效（账号 {account}）", flush=True)
            return 0
        print(
            f"微信公众平台：未登录或已过期（账号 {account}）\n"
            f"请运行: ./wechat-login.sh",
            file=sys.stderr,
            flush=True,
        )
        return 1
    except WechatPublishError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
