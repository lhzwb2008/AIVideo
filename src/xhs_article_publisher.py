"""小红书创作者中心 · 图文笔记草稿（长文入口，填表后点「暂存草稿」）。"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from eastmoney_publisher import (
    _chrome_path,
    _ensure_patchright,
    parse_forum_pack,
    sau_home,
)
from forum_editor_fill import paste_paragraphs
from forum_pack_format import body_to_plaintext
from paths import ROOT
from social_caption import build_social_fields, _truncate


class XhsArticlePublishError(RuntimeError):
    pass


PUBLISH_URL = (
    "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=article"
)
DRAFTS_URL = "https://creator.xiaohongshu.com/creator/note-manage?tab=draft"
ACCOUNT_ENV = "SAU_XHS_ACCOUNT"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def cookie_path(root: Path | None = None, account: str | None = None) -> Path:
    account = account or _env(ACCOUNT_ENV, "main")
    path = sau_home(root) / "cookies" / f"xiaohongshu_{account}.json"
    if not path.is_file():
        raise XhsArticlePublishError(
            f"未找到 cookie: {path}\n请先运行: ./social-login.sh xiaohongshu"
        )
    return path


def profile_dir(root: Path | None = None, account: str | None = None) -> Path:
    account = account or _env(ACCOUNT_ENV, "main")
    return sau_home(root) / "cookies" / "browser_profiles" / f"xiaohongshu_{account}"


def _forum_body_for_note(data: dict, *, max_chars: int = 9000) -> str:
    parts: list[str] = []
    for sec in data.get("sections") or []:
        headline = (sec.get("headline") or "").strip()
        body = (sec.get("body") or "").strip()
        if headline:
            parts.append(headline)
        if body:
            parts.append(body_to_plaintext(body))
    text = "\n\n".join(parts).strip()
    disclaimer = (data.get("disclaimer") or "").strip()
    if disclaimer:
        text = f"{text}\n\n{disclaimer}" if text else disclaimer
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def _note_title(data: dict) -> str:
    title = (data.get("title") or "").strip()
    return _truncate(title, 20)


def _note_tags_line() -> str:
    raw = _env("XHS_HASHTAGS", "#财经 #股市 #投资 #AI")
    tags = [t.strip().lstrip("#") for t in raw.split() if t.strip()]
    return " ".join(f"#{t}" for t in tags[:5])


async def _logged_in(page) -> bool:
    url = (page.url or "").lower()
    if "login" in url:
        return False
    body = await page.evaluate("() => document.body.innerText || ''")
    if "登录" in body[:400] and "扫一扫" in body:
        return False
    return True


async def _open_article_editor(page) -> None:
    timeout_ms = int(_env("AIVIDEO_XHS_GOTO_TIMEOUT_MS", "90000") or "90000")
    await page.goto(
        PUBLISH_URL,
        wait_until="domcontentloaded",
        timeout=timeout_ms,
    )
    await asyncio.sleep(2)
    if not await _logged_in(page):
        raise XhsArticlePublishError(
            "未登录小红书创作中心，请先 ./social-login.sh xiaohongshu"
        )

    for label in ("新的创作", "写长文", "开始创作", "发布笔记"):
        btn = page.get_by_role("button", name=label).first
        if await btn.count():
            try:
                await btn.click(timeout=8_000)
                await asyncio.sleep(2)
                break
            except Exception:
                continue

    title_inp = page.locator('input[placeholder*="填写标题"], input[placeholder*="标题"]')
    await title_inp.first.wait_for(state="visible", timeout=60_000)


async def _fill_title(page, title: str) -> None:
    inp = page.locator('input[placeholder*="填写标题"], input[placeholder*="标题"]').first
    await inp.fill(title[:20])


async def _fill_body(page, body: str, tags_line: str) -> None:
    desc = page.locator('p[data-placeholder*="输入正文"], div[contenteditable="true"]').first
    if not await desc.count():
        desc = page.locator(".ql-editor, .ProseMirror").first
    await desc.click(timeout=10_000)
    await page.keyboard.press("Control+KeyA")
    await page.keyboard.press("Delete")
    chunks = [body]
    if tags_line:
        chunks.append(tags_line)
    await paste_paragraphs(page, [c for c in chunks if c.strip()])


async def _save_draft(page) -> None:
    for label in ("暂存草稿", "保存草稿", "存草稿"):
        btn = page.get_by_role("button", name=label).first
        if await btn.count():
            await btn.click(timeout=15_000)
            await asyncio.sleep(2)
            return
    leave = page.get_by_role("button", name="离开").first
    if await leave.count():
        await leave.click(timeout=8_000)
        confirm = page.get_by_role("button", name=re.compile("保存|暂存|确定")).first
        if await confirm.count():
            await confirm.click(timeout=8_000)
        await asyncio.sleep(2)
        return
    # 创作中心常自动保存，未找到按钮时仍视为草稿已写入编辑页
    await asyncio.sleep(2)


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
    cookie = sau_home() / "cookies" / f"xiaohongshu_{account}.json"
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

    raise XhsArticlePublishError(
        f"未找到 cookie: {cookie}\n请先运行: ./social-login.sh xiaohongshu"
    )


async def publish_forum_pack(
    pack_dir: Path,
    *,
    headless: bool = True,
    account: str | None = None,
    script: dict | None = None,
) -> dict:
    """填好图文笔记并暂存草稿，不点「发布」。"""
    _ensure_patchright()
    from patchright.async_api import async_playwright

    data = parse_forum_pack(pack_dir)
    cookie = cookie_path(account=account)
    title = _note_title(data)
    body = _forum_body_for_note(data)
    tags_line = _note_tags_line()
    if script:
        social = build_social_fields(script, "xiaohongshu")
        if social.get("title"):
            title = social["title"][:20]

    async with async_playwright() as p:
        context, cookie = await _launch_context(
            p, headless=headless, account=account
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await _open_article_editor(page)
            await _fill_title(page, title)
            await _fill_body(page, body, tags_line)
            await _save_draft(page)
            await context.storage_state(path=str(cookie))
            return {
                "title": title,
                "original_title": data["title"],
                "pack_dir": data["pack_dir"],
                "draft_only": True,
                "published": False,
                "url": DRAFTS_URL,
            }
        finally:
            await context.close()
