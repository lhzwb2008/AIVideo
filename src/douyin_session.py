#!/usr/bin/env python3
"""抖音创作者登录态校验（以上传页 file input 为准）。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from douyin_publisher import DouyinPublishError, _ensure_patchright, cookie_path
from paths import ROOT
from sau_client import SauError, check_douyin_session, douyin_account


async def verify_upload_page(*, root: Path | None = None, account: str | None = None) -> bool:
    """返回 True 表示 cookie 能打开上传页且出现 file input。"""
    _ensure_patchright()
    from patchright.async_api import async_playwright

    root = root or ROOT
    cookie = cookie_path(root, account)

    launch: dict = {
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled", "--lang=zh-CN"],
    }
    chrome = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if chrome.is_file():
        launch["executable_path"] = str(chrome)
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
            await page.goto(
                "https://creator.douyin.com/creator-micro/content/upload",
                wait_until="domcontentloaded",
                timeout=90_000,
            )
            url = page.url.lower()
            if "passport" in url or "/login" in url:
                return False
            for text in ("扫码登录", "手机号登录"):
                loc = page.get_by_text(text, exact=False).first
                if await loc.count():
                    try:
                        if await loc.is_visible():
                            return False
                    except Exception:
                        pass
            selectors = (
                "input.semi-upload-hidden-input",
                "input[type='file'][accept*='video']",
                "input[type='file']",
            )
            for _ in range(20):
                for sel in selectors:
                    loc = page.locator(sel).first
                    if await loc.count():
                        return True
                await asyncio.sleep(1)
            return False
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="校验抖音上传页登录态")
    parser.add_argument("--account", default=douyin_account())
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    try:
        check_douyin_session(root=ROOT)
    except SauError as exc:
        if not args.quiet:
            print(str(exc), file=sys.stderr)
        return 1

    try:
        ok = asyncio.run(verify_upload_page(root=ROOT, account=args.account))
    except DouyinPublishError as exc:
        if not args.quiet:
            print(str(exc), file=sys.stderr)
        return 1

    if not ok:
        if not args.quiet:
            print(
                "Cookie 能进首页但上传页未就绪（常见于半失效）。"
                "请运行: ./douyin-login.sh --force",
                file=sys.stderr,
            )
        return 1

    if not args.quiet:
        cookie = cookie_path(ROOT, args.account)
        print(f"登录态有效（已验证上传页）: {cookie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
