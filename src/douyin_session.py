#!/usr/bin/env python3
"""抖音创作者登录态校验（以上传页 file input 为准，支持 Profile / cookie）。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from douyin_publisher import DouyinPublishError, _ensure_patchright, resolve_cookie_path
from paths import ROOT
from sau_client import SauError, check_douyin_session, douyin_account


def profile_dir(*, root: Path | None = None, account: str | None = None) -> Path:
    account = account or douyin_account()
    cookie = resolve_cookie_path(root, account)
    return cookie.parent / "browser_profiles" / cookie.stem


async def verify_upload_page(
    *,
    root: Path | None = None,
    account: str | None = None,
    use_profile: bool = True,
) -> bool:
    """True = 能打开上传页且出现 file input（未要求扫码）。"""
    _ensure_patchright()
    from patchright.async_api import async_playwright

    root = root or ROOT
    account = account or douyin_account()
    cookie = resolve_cookie_path(root, account)
    profile = profile_dir(root=root, account=account)

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
        browser = None
        if use_profile and profile.is_dir() and any(profile.iterdir()):
            context = await p.chromium.launch_persistent_context(
                str(profile),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                viewport={"width": 1440, "height": 900},
                **launch,
            )
        elif cookie.is_file() and cookie.stat().st_size > 64:
            browser = await p.chromium.launch(**launch)
            context = await browser.new_context(
                storage_state=str(cookie),
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
                viewport={"width": 1440, "height": 900},
            )
        else:
            return False

        try:
            page = context.pages[0] if context.pages else await context.new_page()
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
                "div[class^='container'] input[type='file']",
                "div[class^='upload-content'] input",
                "input[type='file']",
            )
            for _ in range(45):
                for sel in selectors:
                    loc = page.locator(sel).first
                    if await loc.count():
                        return True
                await asyncio.sleep(1)
            return False
        finally:
            if browser:
                await browser.close()
            else:
                await context.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="校验抖音上传页登录态")
    parser.add_argument("--account", default=douyin_account())
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    prof = profile_dir(root=ROOT, account=args.account)
    cookie = resolve_cookie_path(ROOT, args.account)
    has_profile = prof.is_dir() and any(prof.iterdir())
    has_cookie = cookie.is_file() and cookie.stat().st_size > 64

    if not has_profile and not has_cookie:
        if not args.quiet:
            print(
                f"未找到登录态（Profile 或 cookie）\n"
                f"  Profile: {prof}\n"
                f"  Cookie: {cookie}\n"
                f"请运行: .\scripts\login-cn.ps1 douyin --force",
                file=sys.stderr,
            )
        return 1

    if has_cookie:
        try:
            check_douyin_session(root=ROOT)
        except SauError:
            if not has_profile:
                if not args.quiet:
                    print(
                        "抖音 cookie 无效。请运行: .\scripts\login-cn.ps1 douyin --force",
                        file=sys.stderr,
                    )
                return 1

    try:
        ok = asyncio.run(
            verify_upload_page(root=ROOT, account=args.account, use_profile=True)
        )
    except DouyinPublishError as exc:
        if not args.quiet:
            print(str(exc), file=sys.stderr)
        return 1

    if not ok:
        if not args.quiet:
            print(
                "登录态无效或上传页未就绪。"
                "请运行: .\scripts\login-cn.ps1 douyin --force",
                file=sys.stderr,
            )
        return 1

    if not args.quiet:
        where = f"Profile: {prof}" if has_profile else f"Cookie: {cookie}"
        print(f"登录态有效（已验证上传页）: {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
