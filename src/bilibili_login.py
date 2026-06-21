#!/usr/bin/env python3
"""B 站创作中心 · 有头扫码登录（持久化 Profile + 同步 cookie）。"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from bilibili_session import (
    HOME_URL,
    UPLOAD_URL,
    BilibiliSessionError,
    bilibili_account,
    cookie_path,
    login_completed,
    profile_dir,
    _chrome_path,
    _ensure_patchright,
    _looks_logged_out,
)
from paths import ROOT


async def _fresh_page(context):
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
    return page


async def _goto(page, url: str) -> None:
    last_exc: Exception | None = None
    for wait_until in ("commit", "domcontentloaded"):
        try:
            await page.goto(url, wait_until=wait_until, timeout=90_000)
            if "bilibili.com" in (page.url or ""):
                return
        except Exception as exc:
            last_exc = exc
            if "bilibili.com" in (page.url or ""):
                return
            await asyncio.sleep(1)
    if last_exc and "bilibili.com" not in (page.url or ""):
        raise BilibiliSessionError(
            f"无法打开 B 站页面（{url}）：{last_exc}\n"
            "请确认网络正常，然后重试: ./bilibili-login.sh --force"
        ) from last_exc


async def _try_open_scan_login(page) -> None:
    for label in ("扫码登录",):
        tab = page.get_by_text(label, exact=False).first
        if not await tab.count():
            continue
        try:
            if await tab.is_visible():
                await tab.click(timeout=3000)
                await asyncio.sleep(0.8)
                return
        except Exception:
            continue


async def login_interactive(
    *,
    root: Path | None = None,
    account: str | None = None,
    timeout_s: float = 300,
) -> Path:
    """有头 Chrome 扫码登录，保存 cookie 与持久化 profile。"""
    _ensure_patchright()
    from patchright.async_api import async_playwright

    root = root or ROOT
    account = account or bilibili_account()
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
            "--window-size=1440,1400",
        ],
    }
    if os.name == "nt":
        launch["args"].extend(["--disable-gpu", "--disable-dev-shm-usage"])
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
            viewport={"width": 1440, "height": 1400},
            **launch,
        )
        page = await _fresh_page(context)

        print("正在打开 B 站创作中心…", flush=True)
        await _goto(page, HOME_URL)
        await asyncio.sleep(1.5)
        await _try_open_scan_login(page)

        if await login_completed(page):
            await asyncio.sleep(1)
            await context.storage_state(path=str(cookie))
            await context.close()
            print(f"已登录，cookie 已同步: {cookie}", flush=True)
            return cookie

        if await _looks_logged_out(page):
            print(
                "已打开 Chrome，请在窗口内用 B 站 App 扫码登录。",
                flush=True,
            )
        else:
            print("页面已加载，若未登录请按提示完成验证…", flush=True)
        print(f"创作中心: {HOME_URL}", flush=True)
        print("若窗口被挡住，请从任务栏点 Google Chrome 切到前台。", flush=True)

        deadline = asyncio.get_event_loop().time() + timeout_s
        last_hint = 0.0
        while asyncio.get_event_loop().time() < deadline:
            if await login_completed(page):
                try:
                    await _goto(page, UPLOAD_URL)
                except Exception:
                    pass
                await asyncio.sleep(2)
                await context.storage_state(path=str(cookie))
                await context.close()
                print("登录成功，profile 与 cookie 已保存:", flush=True)
                print(f"  Profile: {profile}", flush=True)
                print(f"  Cookie:  {cookie}", flush=True)
                return cookie

            now = asyncio.get_event_loop().time()
            if now - last_hint > 30:
                if await _looks_logged_out(page):
                    state = "等待扫码"
                elif (page.url or "").startswith("chrome-error"):
                    state = "网络异常（请检查 VPN/代理，可手动在窗口刷新一次）"
                else:
                    state = "等待平台确认登录"
                print(f"仍在{state}… 当前: {page.url}", flush=True)
                last_hint = now
            await asyncio.sleep(3)

        await context.close()

    raise BilibiliSessionError(
        "登录超时（5 分钟内未完成扫码）。"
        "请关闭 VPN，确认手机和电脑同一网络，然后重试: ./bilibili-login.sh --force"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="B 站创作中心扫码登录")
    parser.add_argument("--account", default=bilibili_account())
    parser.add_argument("--login", action="store_true", help="打开有头 Chrome 扫码登录")
    parser.add_argument("--check", action="store_true", help="只校验登录态")
    parser.add_argument("--force", action="store_true", help="（由 shell 脚本处理）强制重新登录")
    parser.add_argument("--timeout", type=int, default=300, help="登录等待秒数（默认 300）")
    args = parser.parse_args()

    if args.check:
        from bilibili_session import verify_upload_page

        ok = asyncio.run(verify_upload_page(root=ROOT, account=args.account))
        if ok:
            print("B 站登录态有效")
            return 0
        print("B 站登录态无效", file=sys.stderr)
        return 1

    try:
        if args.login:
            asyncio.run(
                login_interactive(
                    root=ROOT,
                    account=args.account,
                    timeout_s=float(args.timeout),
                )
            )
            print("登录成功，Profile 与 cookie 已保存。", flush=True)
            return 0

        parser.print_help()
        return 2
    except BilibiliSessionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        msg = str(exc)
        if "Target.createTarget" in msg or "Failed to open a new tab" in msg:
            print(
                "登录异常: Chrome 无法打开标签页。"
                "请先关闭所有 Chrome 窗口，再执行: .\\scripts\\login-cn.ps1 bilibili --force",
                file=sys.stderr,
            )
        else:
            print(f"登录异常: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
