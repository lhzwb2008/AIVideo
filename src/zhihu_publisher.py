"""知乎专栏 · 论坛图文（Playwright 填表 + 草稿/自动发布）。"""

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


def _auto_publish_enabled() -> bool:
    return _env("ZHIHU_AUTO_PUBLISH", "0").lower() in ("1", "true", "yes", "on")


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


async def _editor_image_count(page) -> int:
    from forum_editor_fill import ZHIHU_EDITOR_LOCATOR

    return await page.locator(
        f"{ZHIHU_EDITOR_LOCATOR} img, .DraftEditor-root img"
    ).count()


async def _wait_editor_image_count(page, expected: int) -> int:
    last_count = 0
    for _ in range(60):
        last_count = await _editor_image_count(page)
        if last_count >= expected:
            await asyncio.sleep(1.0)
            return await _editor_image_count(page)
        await asyncio.sleep(1)
    raise ZhihuPublishError(
        f"知乎正文图片数量不足：期望 {expected} 张，编辑器仅检测到 {last_count} 张"
    )


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


async def _click_publish(page) -> str:
    """写作页点击发布并确认，返回文章 URL（若可解析）。"""
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(0.5)

    pub = None
    for loc in (
        page.get_by_role("button", name="发布").first,
        page.locator("button").filter(has_text="发布").first,
        page.locator('[class*="Publish"]').filter(has_text="发布").first,
    ):
        if await loc.count():
            pub = loc
            break
    if pub is None:
        raise ZhihuPublishError("未找到「发布」按钮")

    await pub.scroll_into_view_if_needed()
    await pub.click(timeout=15_000, force=True)
    await asyncio.sleep(1.5)

    for _ in range(8):
        for label in ("确认发布", "继续发布", "确定", "发布"):
            btn = page.get_by_role("button", name=label).first
            if await btn.count() and await btn.is_visible():
                try:
                    await btn.click(timeout=8000, force=True)
                    await asyncio.sleep(1.2)
                except Exception:
                    pass
        url = page.url
        if "/p/" in url and "zhuanlan.zhihu.com" in url:
            return url.split("?")[0]
        body = await page.evaluate("() => document.body.innerText || ''")
        if any(k in body for k in ("发布成功", "已发布", "提交成功")):
            if "/p/" in page.url:
                return page.url.split("?")[0]
            break
        await asyncio.sleep(1.5)

    # 发布后可能跳转到文章页或内容管理
    url = page.url
    if "/p/" in url and "zhuanlan.zhihu.com" in url:
        return url.split("?")[0]

    await page.goto(DRAFTS_URL, wait_until="domcontentloaded", timeout=60_000)
    await asyncio.sleep(2)
    raise ZhihuPublishError(
        "已点击发布但未检测到成功页，请到草稿箱/内容管理确认。"
        f" 当前 URL: {page.url}"
    )


async def _delete_draft_by_title(page, title: str) -> int:
    await page.goto(DRAFTS_URL, wait_until="domcontentloaded", timeout=90_000)
    await asyncio.sleep(2)
    if "login" in page.url.lower():
        raise ZhihuPublishError("未登录知乎草稿箱，无法清除草稿")
    snippet = title.strip()[:24]
    if not snippet:
        return 0

    deleted = 0
    for _ in range(8):
        action = await page.evaluate(
            """(snippet) => {
              const rows = [...document.querySelectorAll('div, li, article')];
              for (const el of rows) {
                const t = (el.innerText || '');
                if (t.length > 900 || !t.includes(snippet)) continue;
                el.scrollIntoView({ block: 'center', inline: 'nearest' });
                const controls = [...el.querySelectorAll('button, a, span, div')];
                const del = controls.find((b) => /删除/.test((b.innerText || '').trim()));
                if (del) { del.click(); return 'delete'; }
                const more = controls.find((b) => /更多|\\.\\.\\.|···/.test((b.innerText || '').trim()));
                if (more) { more.click(); return 'more'; }
                return 'blocked';
              }
              return 'done';
            }""",
            snippet,
        )
        if action == "done":
            break
        if action == "blocked":
            break
        await asyncio.sleep(0.6)
        for label in ("删除", "确定", "确认"):
            btn = page.get_by_role("button", name=label).first
            if await btn.count():
                await btn.click(timeout=10_000)
                deleted += 1
                await asyncio.sleep(1.5)
                break
    return deleted


async def clear_forum_draft(
    pack_dir: Path,
    *,
    headless: bool | None = None,
    account: str | None = None,
) -> dict:
    _ensure_patchright()
    from patchright.async_api import async_playwright

    if headless is None:
        headless = _headless()

    data = parse_forum_pack(pack_dir)
    cookie_path(account=account)
    async with async_playwright() as p:
        context, cookie = await _launch_context(
            p, headless=headless, account=account
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            deleted = await _delete_draft_by_title(page, data["title"])
            await context.storage_state(path=str(cookie))
            return {
                "title": data["title"],
                "pack_dir": data["pack_dir"],
                "deleted": deleted,
                "url": DRAFTS_URL,
            }
        finally:
            await context.close()


async def publish_forum_pack(
    pack_dir: Path,
    *,
    headless: bool | None = None,
    account: str | None = None,
    draft_only: bool | None = None,
) -> dict:
    """填好专栏；默认存草稿，draft_only=False 时点击发布。"""
    _ensure_patchright()
    from patchright.async_api import async_playwright

    if headless is None:
        headless = _headless()
    if draft_only is None:
        draft_only = not _auto_publish_enabled()

    data = parse_forum_pack(pack_dir)
    expected_image_paths = []
    if data.get("cover"):
        expected_image_paths.append(str(Path(data["cover"]).resolve()))
    for sec in data["sections"]:
        if sec.get("image"):
            expected_image_paths.append(str(Path(sec["image"]).resolve()))
    expected_images = len(dict.fromkeys(expected_image_paths))
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
            actual_images = await _wait_editor_image_count(page, expected_images)
            if actual_images < expected_images:
                raise ZhihuPublishError(
                    f"知乎正文图片数量不足：期望 {expected_images} 张，实际 {actual_images} 张"
                )
            await asyncio.sleep(1)
            article_url = ""
            publish_note = ""
            if draft_only:
                await _save_draft(page)
                draft_url = page.url
                if "draft" not in draft_url.lower() and "write" not in draft_url.lower():
                    draft_url = DRAFTS_URL
                article_url = draft_url
            else:
                try:
                    article_url = await _click_publish(page)
                except ZhihuPublishError as exc:
                    await _save_draft(page)
                    raise ZhihuPublishError(f"{exc}（内容已存草稿）") from exc

            await context.storage_state(path=str(cookie))
            return {
                "title": data["title"],
                "pack_dir": data["pack_dir"],
                "draft_only": draft_only,
                "published": not draft_only,
                "editor_mode": mode,
                "url": article_url or DRAFTS_URL,
                "images": expected_image_paths,
                "image_count": actual_images,
                "expected_image_count": expected_images,
                "publish_note": publish_note,
            }
        finally:
            await context.close()
