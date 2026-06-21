#!/usr/bin/env python3
"""抖音创作者平台 · 有头扫码登录（持久化 Profile + 同步 cookie）。"""

from __future__ import annotations

import argparse
import asyncio
import sys
import os
from pathlib import Path

from douyin_publisher import (
    DouyinPublishError,
    _chrome_path,
    _ensure_patchright,
    resolve_cookie_path,
    sau_home,
)
from paths import ROOT
from sau_client import douyin_account

# 登录走首页；扫码期间不主动跳转 upload（避免 upload ↔ home 闪屏）
HOME_URL = "https://creator.douyin.com/"


def profile_dir(*, root: Path | None = None, account: str | None = None) -> Path:
    account = account or douyin_account()
    cookie = resolve_cookie_path(root, account)
    return cookie.parent / "browser_profiles" / cookie.stem


async def _fresh_page(context):
    """复用 persistent context 默认标签；Windows 上盲目 new_page 常失败。"""
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


async def _page_has_content(page) -> bool:
    try:
        length = await page.evaluate(
            "() => (document.body && document.body.innerText) ? document.body.innerText.trim().length : 0"
        )
        return int(length or 0) > 30
    except Exception:
        return False


async def _goto_douyin(page, url: str) -> None:
    last_exc: Exception | None = None
    for wait_until in ("commit", "domcontentloaded"):
        try:
            await page.goto(url, wait_until=wait_until, timeout=90_000)
            if "douyin.com" in (page.url or ""):
                return
        except Exception as exc:
            last_exc = exc
            if "douyin.com" in (page.url or ""):
                return
            await asyncio.sleep(1)
    if last_exc and "douyin.com" not in (page.url or ""):
        raise DouyinPublishError(
            f"无法打开抖音页面（{url}）：{last_exc}\n"
            "请确认网络正常、已关闭 VPN，然后重试: ./douyin-login.sh --force"
        ) from last_exc


async def _open_login_home(page) -> None:
    """打开创作者首页并等待登录 UI。"""
    await _goto_douyin(page, HOME_URL)
    for attempt in range(3):
        await asyncio.sleep(1 + attempt * 0.5)
        if await _page_has_content(page):
            return
        if attempt < 2:
            try:
                await page.reload(wait_until="domcontentloaded", timeout=60_000)
            except Exception:
                pass
    if not await _page_has_content(page):
        raise DouyinPublishError(
            "Chrome 已打开但抖音首页仍为空白（常见于自动化环境被拦截）。\n"
            "请尝试：1) 关闭 VPN  2) 用系统 Chrome 手动打开 creator.douyin.com\n"
            "3) 再执行: ./douyin-login.sh --force"
        )


async def _dismiss_overlays(page) -> None:
    for text in ("我知道了", "知道了"):
        btn = page.get_by_text(text, exact=True).first
        if not await btn.count():
            continue
        try:
            if await btn.is_visible():
                await btn.click(timeout=2000)
                await asyncio.sleep(0.8)
        except Exception:
            continue


async def _login_ui_visible(page) -> bool:
    for label in ("扫码登录", "手机号登录"):
        loc = page.get_by_text(label, exact=False).first
        if not await loc.count():
            continue
        try:
            if await loc.is_visible():
                return True
        except Exception:
            continue
    qr = page.get_by_role("img", name="二维码").first
    if await qr.count():
        try:
            return await qr.is_visible()
        except Exception:
            pass
    return False


async def _try_open_scan_login(page) -> None:
    if await _login_ui_visible(page):
        return
    for label in ("扫码登录", "抖音扫码"):
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


async def _handle_qrcode_refresh(page) -> bool:
    """仅在仍显示扫码 UI 时刷新二维码，登录后或平台自动跳转时不碰页面。"""
    if not await _login_ui_visible(page):
        return False

    busy = page.get_by_text("系统繁忙").first
    if await busy.count():
        try:
            if await busy.is_visible():
                print("检测到「系统繁忙」，刷新页面…", flush=True)
                await page.reload(wait_until="domcontentloaded", timeout=120_000)
                await asyncio.sleep(2)
                await _try_open_scan_login(page)
                return True
        except Exception:
            pass

    expired = page.get_by_text("二维码失效", exact=True).first
    if await expired.count():
        try:
            if await expired.is_visible():
                print("二维码已失效，点击刷新…", flush=True)
                await expired.click()
                await asyncio.sleep(1)
                return True
        except Exception:
            pass
    return False


async def _session_logged_in(page) -> bool:
    """扫码成功：离开登录页即可，不要求 upload 控件已出现（避免为等控件反复跳转）。"""
    url = (page.url or "").lower()
    if not url or url.startswith("chrome-error") or url in ("about:blank", ""):
        return False
    if "passport" in url or "/login" in url:
        return False
    if await _login_ui_visible(page):
        return False
    if not url.startswith("https://creator.douyin.com"):
        return False
    if "creator-micro" in url:
        return True
    # 部分账号扫码后仍停在首页路径
    return await _page_has_content(page)


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
    account = account or douyin_account()
    cookie = resolve_cookie_path(root, account)
    cookie.parent.mkdir(parents=True, exist_ok=True)
    profile = profile_dir(root=root, account=account)
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
    if os.name == "nt":
        launch["args"].extend(["--disable-gpu", "--disable-dev-shm-usage"])
    chrome = _chrome_path()
    if chrome:
        launch["executable_path"] = chrome
    else:
        launch["channel"] = "chrome"

    # 上传页 deliberately 不注入 stealth（SAU 注释：会导致 SPA 无法渲染上传控件）
    print("正在启动 Chrome…", flush=True)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(profile),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 900},
            **launch,
        )
        page = await _fresh_page(context)

        print("正在打开抖音创作者首页…", flush=True)
        await _open_login_home(page)
        await _dismiss_overlays(page)
        await _try_open_scan_login(page)

        if await _session_logged_in(page):
            await asyncio.sleep(1)
            await context.storage_state(path=str(cookie))
            await context.close()
            print(f"已登录，cookie 已同步: {cookie}", flush=True)
            return cookie

        if not await _login_ui_visible(page):
            print(
                "页面已加载但未检测到扫码入口；若窗口空白，请稍等或 Ctrl+C 后重跑 --force。",
                flush=True,
            )

        print(
            "已打开 Chrome，请在窗口内完成抖音扫码登录（不要扫终端里的旧二维码）。",
            flush=True,
        )
        print("扫码期间页面不会自动跳转，请安心扫码。", flush=True)
        print("若窗口被挡住，请从任务栏点 Google Chrome 切到前台。", flush=True)

        deadline = asyncio.get_event_loop().time() + timeout_s
        last_hint = 0.0
        while asyncio.get_event_loop().time() < deadline:
            # 只被动检测，不 goto/reload —— 避免 upload ↔ home 闪屏
            if await _session_logged_in(page):
                await asyncio.sleep(2)
                await context.storage_state(path=str(cookie))
                await context.close()
                print("登录成功，profile 与 cookie 已保存:", flush=True)
                print(f"  Profile: {profile}", flush=True)
                print(f"  Cookie:  {cookie}", flush=True)
                return cookie

            if await _login_ui_visible(page):
                await _handle_qrcode_refresh(page)
                await _dismiss_overlays(page)

            now = asyncio.get_event_loop().time()
            if now - last_hint > 30:
                if await _login_ui_visible(page):
                    state = "等待扫码"
                elif (page.url or "").startswith("chrome-error"):
                    state = "网络异常（请检查 VPN/代理，可手动在窗口刷新一次）"
                else:
                    state = "等待平台确认登录"
                print(f"仍在{state}… 当前: {page.url}", flush=True)
                last_hint = now
            await asyncio.sleep(3)

        await context.close()

    raise DouyinPublishError(
        "登录超时（5 分钟内未完成扫码）。"
        "请关闭 VPN，确认手机和电脑同一网络，然后重试: ./douyin-login.sh --force"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="抖音创作者平台扫码登录")
    parser.add_argument("--account", default=douyin_account())
    parser.add_argument("--login", action="store_true", help="打开有头 Chrome 扫码登录")
    parser.add_argument("--timeout", type=int, default=300, help="登录等待秒数（默认 300）")
    args = parser.parse_args()

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

        print("请使用 --login", file=sys.stderr)
        return 2
    except (DouyinPublishError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        msg = str(exc)
        if "Target.createTarget" in msg or "Failed to open a new tab" in msg:
            print(
                "登录异常: Chrome 无法打开标签页。"
                "请先关闭所有 Chrome 窗口，再执行: .\\scripts\\login-cn.ps1 douyin --force",
                file=sys.stderr,
            )
        else:
            print(f"登录异常: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
