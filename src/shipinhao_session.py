#!/usr/bin/env python3
"""微信视频号 · 登录态校验（以上传页 file input 为准）。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from paths import ROOT
from sau_paths import chrome_executable, ensure_patchright_import
LOGIN_URL = "https://channels.weixin.qq.com/login.html"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def sau_home(root: Path | None = None) -> Path:
    custom = _env("SAU_HOME")
    if custom:
        return Path(custom).expanduser()
    return (root or ROOT) / "vendor" / "social-auto-upload"


def shipinhao_account() -> str:
    return _env("SAU_SHIPINHAO_ACCOUNT", "main")


def cookie_path(*, root: Path | None = None, account: str | None = None) -> Path:
    account = account or shipinhao_account()
    return sau_home(root) / "cookies" / f"tencent_{account}.json"


def profile_dir(*, root: Path | None = None, account: str | None = None) -> Path:
    account = account or shipinhao_account()
    return sau_home(root) / "cookies" / "browser_profiles" / f"tencent_{account}"


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
    if "login.html" in url or "/login" in url:
        return True
    for text in ("扫码登录", "微信扫码登录", "手机号登录", "微信扫码登录 视频号助手"):
        if await _is_visible(page.get_by_text(text, exact=False).first):
            return True
    if await _is_visible(page.locator("div.login-qrcode-wrap, img.qrcode").first):
        return True
    return False


async def login_completed(page) -> bool:
    """已离开登录页且能进入创作者后台（不触发跳转）。"""
    if await _looks_logged_out(page):
        return False
    url = (page.url or "").lower()
    if any(
        seg in url
        for seg in (
            "platform/post/create",
            "platform/post/list",
            "platform/home",
        )
    ):
        return True
    if await page.locator('input[type="file"]').count():
        return True
    for text in ("发表视频", "添加视频", "内容管理"):
        if await page.get_by_text(text, exact=False).count():
            return True
    return False


async def _upload_ready(page) -> bool:
    if await _looks_logged_out(page):
        return False
    return await login_completed(page)


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
    account = account or shipinhao_account()
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
            await page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60_000)
            for _ in range(20):
                if await _upload_ready(page):
                    return True
                if await _looks_logged_out(page):
                    return False
                await asyncio.sleep(1)
            return False
        finally:
            if browser:
                await browser.close()
            else:
                await context.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="校验视频号上传页登录态")
    parser.add_argument("--account", default=shipinhao_account())
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    ok = asyncio.run(
        verify_upload_page(root=ROOT, account=args.account, use_profile=True)
    )
    if not ok:
        if not args.quiet:
            print(
                "视频号未登录或 cookie/profile 已失效。\n"
                "请运行: ./social-login.sh shipinhao",
                file=sys.stderr,
            )
        return 1

    if not args.quiet:
        profile = profile_dir(root=ROOT, account=args.account)
        cookie = cookie_path(root=ROOT, account=args.account)
        print(f"登录态有效（已验证上传页）")
        print(f"  Profile: {profile}")
        print(f"  Cookie:  {cookie}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
