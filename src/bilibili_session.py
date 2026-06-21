#!/usr/bin/env python3
"""B 站创作中心 · 登录态校验（以上传页为准，支持 Profile / cookie）。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from paths import ROOT
from sau_paths import chrome_executable, ensure_patchright_import

HOME_URL = "https://member.bilibili.com/platform/home"
UPLOAD_URL = "https://member.bilibili.com/platform/upload/video/frame"


class BilibiliSessionError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def sau_home(root: Path | None = None) -> Path:
    custom = _env("SAU_HOME")
    if custom:
        return Path(custom).expanduser()
    return (root or ROOT) / "vendor" / "social-auto-upload"


def bilibili_account() -> str:
    return _env("SAU_BILIBILI_ACCOUNT", "main")


def cookie_path(*, root: Path | None = None, account: str | None = None) -> Path:
    account = account or bilibili_account()
    return sau_home(root) / "cookies" / f"bilibili_{account}.json"


def profile_dir(*, root: Path | None = None, account: str | None = None) -> Path:
    account = account or bilibili_account()
    return sau_home(root) / "cookies" / "browser_profiles" / f"bilibili_{account}"


def _chrome_path() -> str:
    return chrome_executable()


def _ensure_patchright() -> None:
    ensure_patchright_import(sau_home())


async def _is_visible(locator) -> bool:
    try:
        return await locator.is_visible()
    except Exception:
        return False


async def _looks_logged_out(page) -> bool:
    url = (page.url or "").lower()
    if "passport.bilibili.com" in url or "/login" in url:
        return True
    for text in ("扫码登录", "密码登录", "短信登录", "账号登录"):
        if await _is_visible(page.get_by_text(text, exact=False).first):
            return True
    return False


async def upload_page_ready(page) -> bool:
    if await _looks_logged_out(page):
        return False
    url = (page.url or "").lower()
    if "member.bilibili.com" not in url:
        return False
    selectors = (
        'input[placeholder*="标题"]',
        'input[placeholder*="请输入"]',
        'input[type="file"]',
        'button:has-text("上传视频")',
        'div:has-text("上传视频")',
    )
    for sel in selectors:
        loc = page.locator(sel).first
        if await loc.count():
            try:
                if await loc.is_visible():
                    return True
            except Exception:
                continue
    for text in ("上传视频", "稿件管理", "创作中心"):
        if await _is_visible(page.get_by_text(text, exact=False).first):
            return True
    return False


async def login_completed(page) -> bool:
    if await _looks_logged_out(page):
        return False
    url = (page.url or "").lower()
    if "member.bilibili.com" in url:
        return True
    return await upload_page_ready(page)


async def verify_upload_page(
    *,
    root: Path | None = None,
    account: str | None = None,
    use_profile: bool = True,
) -> bool:
    """True = 能打开上传页且未要求扫码。"""
    _ensure_patchright()
    from patchright.async_api import async_playwright

    root = root or ROOT
    account = account or bilibili_account()
    cookie = cookie_path(root=root, account=account)
    profile = profile_dir(root=root, account=account)

    launch: dict = {
        "headless": True,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--lang=zh-CN",
            "--no-first-run",
        ],
    }
    chrome = _chrome_path()
    if chrome:
        launch["executable_path"] = chrome
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
                UPLOAD_URL,
                wait_until="domcontentloaded",
                timeout=90_000,
            )
            for _ in range(30):
                if await upload_page_ready(page):
                    return True
                await asyncio.sleep(1)
            return False
        finally:
            if browser:
                await browser.close()
            else:
                await context.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 B 站创作中心登录态")
    parser.add_argument("--account", default=bilibili_account())
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    prof = profile_dir(root=ROOT, account=args.account)
    cookie = cookie_path(root=ROOT, account=args.account)
    has_profile = prof.is_dir() and any(prof.iterdir())
    has_cookie = cookie.is_file() and cookie.stat().st_size > 64

    if not has_profile and not has_cookie:
        if not args.quiet:
            print(
                f"未找到登录态（Profile 或 cookie）\n"
                f"  Profile: {prof}\n"
                f"  Cookie: {cookie}\n"
                f"请运行: ./bilibili-login.sh --force",
                file=sys.stderr,
            )
        return 1

    try:
        ok = asyncio.run(
            verify_upload_page(root=ROOT, account=args.account, use_profile=True)
        )
    except Exception as exc:
        if not args.quiet:
            print(str(exc), file=sys.stderr)
        return 1

    if not ok:
        if not args.quiet:
            print(
                "登录态无效或上传页未就绪。"
                "请运行: ./bilibili-login.sh --force",
                file=sys.stderr,
            )
        return 1

    if not args.quiet:
        where = f"Profile: {prof}" if has_profile else f"Cookie: {cookie}"
        print(f"登录态有效（已验证上传页）: {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
