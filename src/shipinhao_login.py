#!/usr/bin/env python3
"""微信视频号 · 有头扫码登录（持久化 Chrome Profile + 同步 cookie）。"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from paths import ROOT
from shipinhao_session import (
    LOGIN_URL,
    UPLOAD_URL,
    _chrome_path,
    _ensure_patchright,
    _looks_logged_out,
    _upload_ready,
    cookie_path,
    login_completed,
    profile_dir,
    shipinhao_account,
    verify_upload_page,
)


class ShipinhaoLoginError(RuntimeError):
    pass


async def _goto(page, url: str) -> None:
    for wait_until in ("commit", "domcontentloaded"):
        try:
            await page.goto(url, wait_until=wait_until, timeout=90_000)
            if "channels.weixin.qq.com" in page.url:
                return
        except Exception:
            if "channels.weixin.qq.com" in (page.url or ""):
                return
    await page.goto(url, wait_until="domcontentloaded", timeout=90_000)


async def login_interactive(
    *,
    root: Path | None = None,
    account: str | None = None,
    timeout_s: float = 300,
) -> Path:
    """有头 Chrome 扫码登录，写入 browser_profiles/tencent_* 并同步 tencent_*.json。"""
    _ensure_patchright()
    from patchright.async_api import async_playwright

    root = root or ROOT
    account = account or shipinhao_account()
    cookie = cookie_path(root=root, account=account)
    profile = profile_dir(root=root, account=account)
    cookie.parent.mkdir(parents=True, exist_ok=True)
    profile.mkdir(parents=True, exist_ok=True)

    launch: dict = {
        "headless": False,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--lang=zh-CN",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1440,900",
        ],
    }
    chrome = _chrome_path()
    if chrome:
        launch["executable_path"] = chrome
    else:
        launch["channel"] = "chrome"

    print("正在启动 Chrome…", flush=True)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 900},
            **launch,
        )
        await asyncio.sleep(0.8)
        if context.pages:
            page = context.pages[0]
            for extra in list(context.pages[1:]):
                try:
                    await extra.close()
                except Exception:
                    pass
        else:
            page = await context.new_page()
        try:
            await page.bring_to_front()
        except Exception:
            pass

        print("正在打开视频号登录页…", flush=True)
        await _goto(page, LOGIN_URL)

        if await login_completed(page):
            if "post/create" not in (page.url or ""):
                await _goto(page, UPLOAD_URL)
            await asyncio.sleep(1)
            await context.storage_state(path=str(cookie))
            await context.close()
            print(f"已登录，cookie 已同步: {cookie}", flush=True)
            return cookie

        print(
            "已打开 Chrome，请用微信扫码登录视频号助手（窗口内二维码）。",
            flush=True,
        )
        print("扫码期间页面不会自动跳转，请安心扫码。", flush=True)
        print("若窗口被挡住，请从任务栏点 Google Chrome 切到前台。", flush=True)

        deadline = asyncio.get_event_loop().time() + timeout_s
        last_hint = 0.0
        while asyncio.get_event_loop().time() < deadline:
            # 只被动检测，不跳转 —— 避免 post/create ↔ login.html 闪屏
            if await login_completed(page):
                if "post/create" not in (page.url or ""):
                    await _goto(page, UPLOAD_URL)
                await asyncio.sleep(2)
                if await _upload_ready(page):
                    await context.storage_state(path=str(cookie))
                    await context.close()
                    print("登录成功，profile 与 cookie 已保存:", flush=True)
                    print(f"  Profile: {profile}", flush=True)
                    print(f"  Cookie:  {cookie}", flush=True)
                    return cookie

            now = asyncio.get_event_loop().time()
            if now - last_hint > 30:
                state = "等待扫码" if await _looks_logged_out(page) else "等待页面确认"
                print(f"仍在{state}… 当前: {page.url}", flush=True)
                last_hint = now
            await asyncio.sleep(3)

        await context.close()

    raise ShipinhaoLoginError(
        "登录超时（5 分钟内未完成扫码）。请重试: ./social-login.sh shipinhao --force"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="微信视频号扫码登录")
    parser.add_argument("--account", default=shipinhao_account())
    parser.add_argument("--login", action="store_true", help="打开有头 Chrome 扫码登录")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    try:
        if not args.login:
            print("请使用 --login", file=sys.stderr)
            return 2
        asyncio.run(
            login_interactive(
                root=ROOT,
                account=args.account,
                timeout_s=float(args.timeout),
            )
        )
        ok = asyncio.run(
            verify_upload_page(root=ROOT, account=args.account, use_profile=True)
        )
        if not ok:
            print(
                "登录后上传页仍未就绪，请再试: ./social-login.sh shipinhao --force",
                file=sys.stderr,
            )
            return 1
        print("上传页验证通过，可以发布。", flush=True)
        return 0
    except ShipinhaoLoginError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
