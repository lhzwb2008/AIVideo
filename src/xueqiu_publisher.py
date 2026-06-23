"""雪球创作者中心 · 长文图文发布（Playwright）。"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from eastmoney_publisher import (
    _chrome_path,
    _ensure_patchright,
    parse_forum_pack,
    sau_home,
)
from forum_editor_fill import (
    fill_xueqiu_body_sections,
    focus_editor_end,
    move_cursor_to_end,
    prepare_image_upload,
)
from paths import ROOT


class XueqiuPublishError(RuntimeError):
    pass


EDITOR_URL = "https://mp.xueqiu.com/writeV2/?position=pc_creator_post"
ACCOUNT_ENV = "XUEQIU_ACCOUNT"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def cookie_path(root: Path | None = None, account: str | None = None) -> Path:
    account = account or _env(ACCOUNT_ENV, "main")
    path = sau_home(root) / "cookies" / f"xueqiu_{account}.json"
    if not path.is_file():
        raise XueqiuPublishError(
            f"未找到 cookie: {path}\n请先运行: ./xueqiu-login.sh"
        )
    return path


def profile_dir(root: Path | None = None, account: str | None = None) -> Path:
    account = account or _env(ACCOUNT_ENV, "main")
    return sau_home(root) / "cookies" / "browser_profiles" / f"xueqiu_{account}"


def _pick_cover(pack_dir: Path) -> str:
    landscape = pack_dir / "cover_landscape.jpg"
    cover = pack_dir / "cover.jpg"
    if landscape.is_file():
        return str(landscape.resolve())
    if cover.is_file():
        return str(cover.resolve())
    raise XueqiuPublishError(f"缺少 cover.jpg 或 cover_landscape.jpg: {pack_dir}")


def parse_xueqiu_pack(pack_dir: Path) -> dict:
    data = parse_forum_pack(pack_dir)
    data["cover"] = _pick_cover(pack_dir)
    return data


def _title_locator(page):
    return page.locator('textarea[placeholder*="标题"], input[placeholder*="标题"]')


async def _looks_logged_out(page) -> bool:
    """编辑器 UI 可见但未登录（cookie/profile 半失效）。"""
    if "login" in page.url.lower() or "account/login" in page.url.lower():
        return True
    for text in ("未登录", "请登录", "重新登录", "登录帐号", "登录账号"):
        try:
            loc = page.get_by_text(text, exact=False).first
            if await loc.count() and await loc.is_visible():
                return True
        except Exception:
            continue
    return False


async def _editor_ready(page) -> bool:
    if await _looks_logged_out(page):
        return False
    if "writev2" not in page.url.lower():
        return False
    if await _title_locator(page).count():
        return True
    return await page.locator(".ProseMirror").count() > 0


async def _open_longform_editor(page) -> None:
    await page.goto(EDITOR_URL, wait_until="domcontentloaded", timeout=90_000)
    if await _editor_ready(page):
        return
    link = page.get_by_role("link", name="发布长文").first
    if await link.count():
        await link.click(timeout=15_000)
    await page.locator(".ProseMirror").first.wait_for(state="visible", timeout=60_000)


async def _ensure_logged_in(page) -> None:
    if not await _editor_ready(page):
        raise XueqiuPublishError("未登录或未进入长文编辑器")


async def _fill_title(page, title: str) -> None:
    inp = _title_locator(page).first
    await inp.wait_for(state="visible", timeout=30_000)
    await inp.fill(title)


async def _launch_context(p, *, headless: bool, account: str | None):
    chrome = _chrome_path()
    launch: dict = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled", "--lang=zh-CN"],
    }
    if chrome:
        launch["executable_path"] = chrome
    else:
        launch["channel"] = "chrome"

    account = account or _env(ACCOUNT_ENV, "main")
    cookie = sau_home() / "cookies" / f"xueqiu_{account}.json"
    cookie.parent.mkdir(parents=True, exist_ok=True)
    profile = profile_dir(account=account)
    profile.mkdir(parents=True, exist_ok=True)

    if profile.is_dir() and any(profile.iterdir()):
        context = await p.chromium.launch_persistent_context(
            str(profile),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 1000},
            **launch,
        )
        return context, cookie

    if cookie.is_file() and cookie.stat().st_size > 64:
        browser = await p.chromium.launch(**launch)
        context = await browser.new_context(
            storage_state=str(cookie),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 1000},
            permissions=["clipboard-read", "clipboard-write"],
        )
        return context, cookie

    browser = await p.chromium.launch(**launch)
    context = await browser.new_context(
        storage_state=str(cookie) if cookie.is_file() else None,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        viewport={"width": 1440, "height": 1000},
        permissions=["clipboard-read", "clipboard-write"],
    )
    return context, cookie


async def _insert_body_image(page, image_path: str) -> None:
    if "login" in page.url.lower():
        raise XueqiuPublishError("插入配图时跳转到登录页，请重新 ./xueqiu-login.sh")

    await focus_editor_end(page)
    await page.keyboard.press("Enter")

    before = await page.locator(".ProseMirror img").count()
    file_input = page.locator(
        '[class*="toolbar"] input[type="file"][accept*="image"]'
    ).first
    await file_input.wait_for(state="attached", timeout=15_000)
    await file_input.set_input_files(prepare_image_upload(image_path))

    for _ in range(60):
        after = await page.locator(".ProseMirror img").count()
        if after > before:
            await asyncio.sleep(0.8)
            await move_cursor_to_end(page)
            return
        await asyncio.sleep(1)
    raise XueqiuPublishError(f"正文图片插入失败: {image_path}")


async def _cover_is_set(page) -> bool:
    if await page.locator('[class*="cover-pic-wrap"] img').count():
        return True
    preview = page.locator('[class*="section-right"] img')
    return await preview.count() > 0


async def _upload_cover(page, cover_path: str) -> None:
    if await _cover_is_set(page):
        return
    inp = page.locator('input[class*="input-cover-pic"]').first
    await inp.wait_for(state="attached", timeout=15_000)
    await inp.set_input_files(cover_path)
    for _ in range(45):
        if await _cover_is_set(page):
            await asyncio.sleep(0.8)
            return
        await asyncio.sleep(1)
    raise XueqiuPublishError("封面上传后未检测到预览图")


def _publish_button(page):
    return page.locator('button[class*="publish_button-dark"]').first


async def _publish_enabled(page) -> bool:
    pub = _publish_button(page)
    if not await pub.count():
        return False
    cls = await pub.get_attribute("class") or ""
    if "publish_disabled" in cls or "disabled" in cls.split():
        return False
    disabled = await pub.get_attribute("disabled")
    return disabled is None


async def _publish_succeeded(page) -> bool:
    """是否检测到发布成功（toast / 跳转到文章列表）。"""
    try:
        if await page.get_by_text("发布成功", exact=False).count():
            return True
    except Exception:
        pass
    url = (page.url or "").lower()
    return "list/article" in url or "creator/article" in url


async def _confirm_publish_dialog(page) -> bool:
    """点掉发布后弹出的二次确认框里的「发布/确认发布/确定」按钮。"""
    for text in ("确认发布", "继续发布", "确定发布", "发布", "确定"):
        try:
            btn = page.locator("button").filter(has_text=text).first
            if await btn.count() and await btn.is_visible():
                await btn.click(timeout=3000, force=True)
                await asyncio.sleep(1)
                return True
        except Exception:
            continue
    return False


async def _click_publish(page) -> None:
    pub = _publish_button(page)
    await pub.wait_for(state="attached", timeout=30_000)
    for _ in range(60):
        if await _publish_enabled(page):
            break
        await asyncio.sleep(1)
    else:
        raise XueqiuPublishError("发布按钮仍不可用（请检查标题/正文/封面）")

    await pub.click(timeout=15_000, force=True)

    # 雪球长文点「发布」后常弹二次确认框（选专栏/原创声明/确认），
    # 必须在确认框里再点一次真正的发布，否则文章不会真正提交。
    for _ in range(40):
        await asyncio.sleep(1)
        if await _publish_succeeded(page):
            return
        await _confirm_publish_dialog(page)

    # 未确认到成功：截图并报错，避免流水线误判“已发布”。
    try:
        shot = ROOT / "logs" / "xueqiu_publish_fail.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(shot), full_page=True)
        print(f"[发布雪球] 未确认发布成功，已截图: {shot}", flush=True)
    except Exception:
        pass
    raise XueqiuPublishError(
        "点击发布后未确认到「发布成功」（可能弹出未处理的确认框/必填项，或被风控拦截）"
    )


async def publish_forum_pack(
    pack_dir: Path,
    *,
    headless: bool = True,
    draft_only: bool = True,
    account: str | None = None,
) -> dict:
    _ensure_patchright()
    from patchright.async_api import async_playwright

    data = parse_xueqiu_pack(pack_dir)
    try:
        cookie = cookie_path(account=account)
    except XueqiuPublishError:
        cookie = sau_home() / "cookies" / f"xueqiu_{account or 'main'}.json"

    async with async_playwright() as p:
        context, cookie = await _launch_context(
            p, headless=headless, account=account
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await _open_longform_editor(page)
            await _ensure_logged_in(page)

            await _fill_title(page, data["title"])
            await _upload_cover(page, data["cover"])
            await fill_xueqiu_body_sections(
                page,
                data["sections"],
                disclaimer=data.get("disclaimer") or "",
                insert_image=_insert_body_image,
                cover_image=data.get("cover"),
            )

            await asyncio.sleep(2)
            published = False
            if draft_only:
                preview = page.get_by_role("button", name="预览").first
                if await preview.count():
                    await preview.click(timeout=10_000)
                    await asyncio.sleep(2)
            else:
                await _click_publish(page)
                published = True

            await context.storage_state(path=str(cookie))
            return {
                "title": data["title"],
                "pack_dir": data["pack_dir"],
                "cover": data["cover"],
                "images": [
                    data.get("cover"),
                    *[s.get("image") for s in data["sections"] if s.get("image")],
                ],
                "draft_only": draft_only,
                "published": published,
                "url": page.url,
            }
        finally:
            await context.close()
