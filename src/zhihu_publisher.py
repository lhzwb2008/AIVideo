"""知乎专栏 · 论坛图文草稿（Playwright 填表 + 保存草稿，不点发布）。"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from eastmoney_publisher import (
    _chrome_path,
    _ensure_patchright,
    parse_forum_pack,
    sau_home,
)
from forum_editor_fill import (
    fill_zhihu_body_sections,
    prepare_image_upload,
)
from paths import ROOT


class ZhihuPublishError(RuntimeError):
    pass


WRITE_URL = "https://zhuanlan.zhihu.com/write"
DRAFTS_URL = "https://zhuanlan.zhihu.com/creator/manage/drafts"
ACCOUNT_ENV = "ZHIHU_ACCOUNT"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _headless() -> bool:
    return _env("ZHIHU_BROWSER_HEADLESS", "1").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def cookie_path(root: Path | None = None, account: str | None = None) -> Path:
    account = account or _env(ACCOUNT_ENV, "main")
    path = sau_home(root) / "cookies" / f"zhihu_{account}.json"
    if not path.is_file():
        raise ZhihuPublishError(
            f"未找到 cookie: {path}\n请先运行: ./zhihu-login.sh"
        )
    return path


def profile_dir(root: Path | None = None, account: str | None = None) -> Path:
    account = account or _env(ACCOUNT_ENV, "main")
    return sau_home(root) / "cookies" / "browser_profiles" / f"zhihu_{account}"


def _title_locator(page):
    return page.locator(
        'textarea[placeholder*="标题"], input[placeholder*="标题"], '
        'input[placeholder*="请输入标题"], .WriteIndex-titleInput textarea'
    )


async def _wait_zhihu_editor(page) -> None:
    from forum_editor_fill import ZHIHU_EDITOR_LOCATOR

    await page.locator(ZHIHU_EDITOR_LOCATOR).first.wait_for(
        state="visible", timeout=60_000
    )


async def _editor_ready(page) -> bool:
    url = page.url.lower()
    if "signin" in url or "/login" in url:
        return False
    if await _title_locator(page).count():
        return True
    from forum_editor_fill import ZHIHU_EDITOR_LOCATOR

    return await page.locator(ZHIHU_EDITOR_LOCATOR).count() > 0


async def _open_new_write(page) -> None:
    await page.goto(WRITE_URL, wait_until="domcontentloaded", timeout=90_000)
    await asyncio.sleep(2)
    if await _editor_ready(page):
        await _wait_zhihu_editor(page)
        return
    for label in ("写文章", "写回答", "创作"):
        link = page.get_by_role("link", name=label).first
        if await link.count():
            await link.click(timeout=15_000)
            await asyncio.sleep(2)
            break
    btn = page.get_by_role("button", name="写文章").first
    if await btn.count():
        await btn.click(timeout=15_000)
        await asyncio.sleep(2)
    await _wait_zhihu_editor(page)


async def _open_draft_by_title(page, title: str) -> bool:
    """若草稿箱已有同标题草稿，打开编辑（更新比新建更稳）。"""
    await page.goto(DRAFTS_URL, wait_until="domcontentloaded", timeout=90_000)
    await asyncio.sleep(2)
    if "login" in page.url.lower():
        return False
    snippet = title.strip()[:24]
    if not snippet:
        return False
    candidates = [
        page.locator(f'a:has-text("{snippet}")').first,
        page.get_by_text(snippet, exact=False).first,
        page.locator('[class*="Draft"]').filter(has_text=snippet).first,
    ]
    for loc in candidates:
        if await loc.count():
            try:
                await loc.click(timeout=10_000)
                await asyncio.sleep(2)
                if await _editor_ready(page):
                    await _wait_zhihu_editor(page)
                    return True
            except Exception:
                continue
    return False


async def _open_editor(page, title: str) -> str:
    if await _open_draft_by_title(page, title):
        return "draft"
    await _open_new_write(page)
    return "new"


async def _ensure_logged_in(page) -> None:
    if not await _editor_ready(page):
        raise ZhihuPublishError(
            "未登录或未进入专栏编辑器，请先 ./zhihu-login.sh"
        )


async def _fill_title(page, title: str) -> None:
    inp = _title_locator(page)
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
    cookie = sau_home() / "cookies" / f"zhihu_{account}.json"
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

    raise ZhihuPublishError(f"未找到 cookie: {cookie}\n请先运行: ./zhihu-login.sh")


async def _insert_body_image(page, image_path: str) -> None:
    from forum_editor_fill import ZHIHU_EDITOR_LOCATOR

    before = await page.locator(
        f"{ZHIHU_EDITOR_LOCATOR} img, .DraftEditor-root img"
    ).count()

    for sel in (
        '[class*="Toolbar"] input[type="file"][accept*="image"]',
        '[class*="toolbar"] input[type="file"][accept*="image"]',
        'input[type="file"][accept*="image"]',
    ):
        file_input = page.locator(sel).first
        if await file_input.count():
            await file_input.set_input_files(prepare_image_upload(image_path))
            break
    else:
        img_btn = page.locator(
            'button:has-text("图片"), [aria-label*="图片"], [title*="图片"]'
        ).first
        if await img_btn.count():
            await img_btn.click(timeout=5_000)
            file_input = page.locator('input[type="file"][accept*="image"]').first
            await file_input.wait_for(state="attached", timeout=10_000)
            await file_input.set_input_files(prepare_image_upload(image_path))
        else:
            raise ZhihuPublishError(f"未找到图片上传入口: {image_path}")

    for _ in range(60):
        after = await page.locator(
            f"{ZHIHU_EDITOR_LOCATOR} img, .DraftEditor-root img"
        ).count()
        if after > before:
            await asyncio.sleep(0.8)
            return
        await asyncio.sleep(1)
    raise ZhihuPublishError(f"正文图片插入失败: {image_path}")


async def _save_draft(page) -> None:
    for label in ("保存草稿", "存草稿", "暂存草稿"):
        btn = page.get_by_role("button", name=label).first
        if await btn.count():
            await btn.click(timeout=15_000)
            await asyncio.sleep(2)
            return
    save = page.locator("button").filter(has_text="保存").first
    if await save.count():
        await save.click(timeout=15_000)
        await asyncio.sleep(2)
        return
    # 知乎写作页常自动保存；未找到按钮时仍视为已写入
    await asyncio.sleep(2)


async def publish_forum_pack(
    pack_dir: Path,
    *,
    headless: bool | None = None,
    account: str | None = None,
) -> dict:
    """填好专栏并保存草稿，不点击发布。"""
    _ensure_patchright()
    from patchright.async_api import async_playwright

    if headless is None:
        headless = _headless()

    data = parse_forum_pack(pack_dir)
    cookie = cookie_path(account=account)

    async with async_playwright() as p:
        context, cookie = await _launch_context(
            p, headless=headless, account=account
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            mode = await _open_editor(page, data["title"])
            await _ensure_logged_in(page)

            await _fill_title(page, data["title"])
            await fill_zhihu_body_sections(
                page,
                data["sections"],
                disclaimer=data.get("disclaimer") or "",
                insert_image=_insert_body_image,
                cover_image=data.get("cover"),
            )
            await asyncio.sleep(1)
            await _save_draft(page)

            await context.storage_state(path=str(cookie))
            draft_url = page.url
            if "draft" not in draft_url.lower() and "write" not in draft_url.lower():
                draft_url = DRAFTS_URL
            return {
                "title": data["title"],
                "pack_dir": data["pack_dir"],
                "draft_only": True,
                "published": False,
                "editor_mode": mode,
                "url": draft_url,
                "images": [s.get("image") for s in data["sections"] if s.get("image")],
            }
        finally:
            await context.close()
