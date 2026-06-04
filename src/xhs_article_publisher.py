"""小红书创作者中心 · 图文笔记草稿（上传图片 + 标题/正文，暂存草稿）。"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

from eastmoney_publisher import (
    _chrome_path,
    _ensure_patchright,
    parse_forum_pack,
    sau_home,
)
from forum_pack_format import body_to_plaintext
from paths import ROOT
from social_caption import build_social_fields, _truncate


class XhsArticlePublishError(RuntimeError):
    pass


# 与 vendor 一致：图文笔记（传图后出现「填写标题」）
NOTE_PUBLISH_URL = (
    "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=image"
)
# 云端「笔记管理-草稿」与发布页「草稿箱」不是同一套；图文草稿在浏览器本地
PUBLISH_HOME = "https://creator.xiaohongshu.com/publish/publish?target=image"
DRAFTS_URL = PUBLISH_HOME
DRAFT_HINT = (
    "小红书草稿存在当前浏览器本地（非云端）。"
    " 请运行 ./xhs-open-creator.sh 用同一浏览器配置打开创作中心，"
    " 在发布页点「草稿箱」→「图文笔记」查看。"
)
ACCOUNT_ENV = "SAU_XHS_ACCOUNT"
_MAX_IMAGES = 18
_PASTE_KEY = "Meta+V" if sys.platform == "darwin" else "Control+V"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _headless() -> bool:
    return _env("XHS_ARTICLE_BROWSER_HEADLESS", "1").lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _persistent_context_ui_kwargs(*, headless: bool) -> dict:
    """有头模式勿锁死 viewport，否则发布页中间区域滚不到底部「发布/暂存离开」。"""
    if headless:
        return {"viewport": {"width": 1440, "height": 1000}}
    return {"no_viewport": True}


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


def _collect_images(pack_dir: Path, data: dict) -> list[str]:
    """图文笔记轮播顺序：严格按 post.md 分镜图 01→02→…（首图=笔记封面）。

    cover.jpg 仅用于视频封面/公众号头图，不插入小红书轮播（避免把配图 1 挤到第 2 张）。
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        if not path.is_file():
            return
        key = str(path.resolve())
        if key in seen:
            return
        seen.add(key)
        ordered.append(key)

    for sec in data.get("sections") or []:
        raw = sec.get("image")
        if raw:
            add(Path(raw))
    if not ordered:
        add(pack_dir / "cover.jpg")
    if not ordered:
        raise XhsArticlePublishError(
            f"缺少配图: {pack_dir}（需 post.md 中的 images/xx.jpg 或 cover.jpg）"
        )
    return [str(p) for p in ordered[:_MAX_IMAGES]]


def _forum_body_for_note(data: dict, *, max_chars: int = 900) -> str:
    """图文笔记正文区有字数上限，取摘要 + 前两段要点。"""
    parts: list[str] = []
    summary = ""
    for sec in data.get("sections") or []:
        head = (sec.get("headline") or "").strip()
        if head == "摘要" or not summary:
            body = (sec.get("body") or "").strip()
            if body:
                summary = body_to_plaintext(body)
                break
    if summary:
        parts.append(summary[:400])
    for sec in data.get("sections") or []:
        head = (sec.get("headline") or "").strip()
        if head in ("", "摘要"):
            continue
        body = (sec.get("body") or "").strip()
        if body:
            parts.append(body_to_plaintext(body)[:280])
        if len(parts) >= 3:
            break
    text = "\n\n".join(parts).strip()
    disclaimer = (data.get("disclaimer") or "").strip()
    if disclaimer:
        text = f"{text}\n\n{disclaimer}" if text else disclaimer
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def _note_title(data: dict, script: dict | None) -> str:
    if script:
        social = build_social_fields(script, "xiaohongshu")
        if social.get("title"):
            return social["title"][:20]
    return _truncate((data.get("title") or "").strip(), 20)


def _note_tags_line() -> str:
    raw = _env("XHS_HASHTAGS", "#财经 #股市 #投资 #AI")
    tags = [t.strip().lstrip("#") for t in raw.split() if t.strip()]
    return " ".join(f"#{t}" for t in tags[:5])


async def _logged_in(page) -> bool:
    url = (page.url or "").lower()
    if "login" in url:
        return False
    try:
        body = await page.evaluate("() => document.body.innerText || ''")
    except Exception:
        return True
    if "登录" in body[:500] and "扫一扫" in body:
        return False
    return True


def _title_locator(page):
    return page.locator(
        'input[placeholder*="填写标题"], '
        'input[placeholder*="添加标题"], '
        'textarea[placeholder*="填写标题"]'
    ).first


async def _wait_note_editor(page) -> None:
    """传图完成后会出现标题输入框。"""
    title_inp = _title_locator(page)
    for _ in range(120):
        if await title_inp.is_visible():
            return
        if not await _logged_in(page):
            raise XhsArticlePublishError(
                "未登录小红书创作中心，请先 ./social-login.sh xiaohongshu"
            )
        await asyncio.sleep(1)
    raise XhsArticlePublishError(
        "未进入图文笔记编辑页（传图后应出现「填写标题」）。"
        " 可试 ./scripts/publish-xhs-article.sh <论坛包> --headed 查看页面"
    )


async def _upload_images(page, image_paths: list[str]) -> None:
    timeout_ms = int(_env("AIVIDEO_XHS_GOTO_TIMEOUT_MS", "90000") or "90000")
    await page.goto(
        NOTE_PUBLISH_URL,
        wait_until="domcontentloaded",
        timeout=timeout_ms,
    )
    # SPA 需几秒渲染上传区；勿点侧栏「发布笔记」否则会跳到 target=video
    await asyncio.sleep(5)
    if not await _logged_in(page):
        raise XhsArticlePublishError(
            "未登录小红书创作中心，请先 ./social-login.sh xiaohongshu"
        )

    upload_input = page.locator("div[class^='upload-content'] input").first
    if not await upload_input.count():
        upload_input = page.locator(
            'input[type="file"][accept*="image"], input.upload-input'
        ).first
    await upload_input.wait_for(state="attached", timeout=60_000)
    await upload_input.set_input_files(image_paths)
    await _wait_note_editor(page)


def _title_snippet(title: str, *, n: int = 14) -> str:
    return (title or "").strip()[:n]


async def _open_draft_box(page) -> bool:
    await page.goto(NOTE_PUBLISH_URL, wait_until="domcontentloaded", timeout=90_000)
    await asyncio.sleep(2)
    if not await _logged_in(page):
        return False
    opener = page.get_by_text(re.compile(r"草稿箱")).first
    if not await opener.count():
        return False
    await opener.click(timeout=10_000)
    await asyncio.sleep(1.5)
    await _switch_image_draft_tab(page)
    return True


async def _draft_row_has_title(page, title: str) -> bool:
    """图文笔记列表里是否已有同标题草稿。"""
    if not await _open_draft_box(page):
        return False
    snippet = _title_snippet(title)
    if not snippet:
        return False
    return await page.evaluate(
        """(snippet) => {
          for (const el of document.querySelectorAll('div, li, article')) {
            const t = (el.innerText || '');
            if (t.length > 450 || t.length < snippet.length) continue;
            if (!t.includes(snippet) || !t.includes('保存于')) continue;
            const edit = [...el.querySelectorAll('button, a, span, div')]
              .find((b) => (b.innerText || '').trim() === '编辑');
            if (edit) return true;
          }
          return false;
        }""",
        snippet,
    )


async def _open_draft_by_title(page, title: str) -> bool:
    """点该条草稿的「编辑」，在原文上更新（不新建一篇）。"""
    if not await _open_draft_box(page):
        return False
    snippet = _title_snippet(title)
    if not snippet:
        return False
    clicked = await page.evaluate(
        """(snippet) => {
          for (const el of document.querySelectorAll('div, li, article')) {
            const t = (el.innerText || '');
            if (t.length > 450 || t.length < snippet.length) continue;
            if (!t.includes(snippet) || !t.includes('保存于')) continue;
            const edit = [...el.querySelectorAll('button, a, span, div')]
              .find((b) => (b.innerText || '').trim() === '编辑');
            if (edit) { edit.click(); return true; }
          }
          return false;
        }""",
        snippet,
    )
    if not clicked:
        return False
    await asyncio.sleep(2)
    try:
        await _wait_note_editor(page)
        return True
    except XhsArticlePublishError:
        return False


async def _fill_title(page, title: str) -> None:
    inp = _title_locator(page)
    await inp.wait_for(state="visible", timeout=30_000)
    await inp.fill("")
    await inp.fill(title[:20])


async def _fill_desc(page, body: str, tags_line: str) -> None:
    desc = page.locator('p[data-placeholder*="输入正文描述"]').first
    if not await desc.count():
        desc = page.locator(
            'div[contenteditable="true"][data-placeholder*="正文"], '
            'p[data-placeholder*="正文"]'
        ).first
    await desc.wait_for(state="visible", timeout=30_000)
    await desc.click(timeout=10_000)
    await page.keyboard.press("Control+KeyA")
    await page.keyboard.press("Delete")
    block = body.strip()
    if tags_line:
        block = f"{block}\n\n{tags_line}" if block else tags_line
    if not block:
        return
    try:
        await page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    except Exception:
        pass
    await page.evaluate(
        "async (t) => { await navigator.clipboard.writeText(t); }",
        block,
    )
    await page.keyboard.press(_PASTE_KEY)
    await asyncio.sleep(0.5)


async def _inject_cookies_from_file(context, cookie_file: Path) -> None:
    import json

    try:
        data = json.loads(cookie_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    cookies = data.get("cookies") or []
    if cookies:
        await context.add_cookies(cookies)


def _parse_image_draft_count(body: str) -> int | None:
    m = re.search(r"图文笔记\s*[（(]\s*(\d+)\s*[）)]", body)
    return int(m.group(1)) if m else None


async def _scroll_publish_footer_into_view(page) -> None:
    """发布页主内容在内部滚动容器里，需滚到底才能看到「暂存离开 / 发布」。"""
    await page.evaluate(
        """() => {
          const labels = ['暂存离开', '发布', '暂存草稿'];
          for (const label of labels) {
            const btn = [...document.querySelectorAll('button, [role=button], span, div')]
              .find((b) => (b.innerText || '').trim() === label);
            if (btn) {
              btn.scrollIntoView({ block: 'center', inline: 'nearest' });
              return;
            }
          }
          const scrollables = [];
          for (const el of document.querySelectorAll('*')) {
            const st = getComputedStyle(el);
            if (!/(auto|scroll)/.test(st.overflowY)) continue;
            if (el.scrollHeight <= el.clientHeight + 8) continue;
            scrollables.push(el);
          }
          scrollables.sort((a, b) => b.scrollHeight - a.scrollHeight);
          for (const el of scrollables.slice(0, 6)) {
            el.scrollTop = el.scrollHeight;
          }
          window.scrollTo(0, document.body.scrollHeight);
        }"""
    )
    await asyncio.sleep(0.4)


async def _click_save_draft(page) -> None:
    """图文发布页底部为「暂存离开」（非「暂存草稿」）。"""
    await _scroll_publish_footer_into_view(page)
    labels = ("暂存离开", "暂存草稿", "保存草稿", "存草稿")
    for label in labels:
        for loc in (
            page.get_by_role("button", name=label),
            page.locator(f"button:has-text('{label}')"),
            page.get_by_text(label, exact=True),
        ):
            if await loc.count():
                await loc.first.scroll_into_view_if_needed(timeout=10_000)
                await loc.first.click(timeout=15_000)
                await asyncio.sleep(2)
                return
    raise XhsArticlePublishError(
        "未找到「暂存离开」按钮。请用 ./scripts/publish-xhs-article.sh <包> --headed 查看发布页"
    )


async def _switch_image_draft_tab(page) -> None:
    tab = page.get_by_text(re.compile(r"图文笔记\s*[（(]")).first
    if await tab.count():
        await tab.click(timeout=10_000)
        await asyncio.sleep(0.5)


async def _image_draft_count_in_modal(page) -> int:
    """解析「图文笔记 (N)」；须先切到图文页签（默认常为视频）。"""
    body = await page.evaluate("() => document.body.innerText || ''")
    if "保存成功" in body:
        parsed = _parse_image_draft_count(body)
        if parsed is not None and parsed > 0:
            return parsed

    if "图文笔记" not in body or _parse_image_draft_count(body) in (None, 0):
        opener = page.get_by_text(re.compile(r"草稿箱")).first
        if await opener.count():
            await opener.click(timeout=10_000)
            await asyncio.sleep(1.0)
        await _switch_image_draft_tab(page)

    body = await page.evaluate("() => document.body.innerText || ''")
    parsed = _parse_image_draft_count(body)
    if parsed is not None:
        return parsed
    if "保存成功" in body:
        return 1
    return 0


async def _launch_context(p, *, headless: bool, account: str | None):
    """始终用持久化浏览器配置，草稿才能留在本机同一 Profile 里。"""
    chrome = _chrome_path()
    args = ["--disable-blink-features=AutomationControlled", "--lang=zh-CN"]
    if not headless:
        args.append("--start-maximized")
    launch: dict = {"headless": headless, "args": args}
    if chrome:
        launch["executable_path"] = chrome
    else:
        launch["channel"] = "chrome"

    account = account or _env(ACCOUNT_ENV, "main")
    cookie = cookie_path(account=account)
    profile = profile_dir(account=account)
    profile.mkdir(parents=True, exist_ok=True)

    context = await p.chromium.launch_persistent_context(
        str(profile),
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        permissions=["clipboard-read", "clipboard-write"],
        **_persistent_context_ui_kwargs(headless=headless),
        **launch,
    )
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto(PUBLISH_HOME, wait_until="domcontentloaded", timeout=90_000)
    await asyncio.sleep(2)
    if not await _logged_in(page) and cookie.is_file():
        await _inject_cookies_from_file(context, cookie)
        await page.goto(PUBLISH_HOME, wait_until="domcontentloaded", timeout=90_000)
        await asyncio.sleep(2)
    if not await _logged_in(page):
        await context.close()
        raise XhsArticlePublishError(
            "创作中心未登录。请先 ./social-login.sh xiaohongshu，"
            " 再运行 ./scripts/publish-xhs-article.sh"
        )
    return context, cookie


async def open_creator_browser(*, account: str | None = None) -> None:
    """有头打开与发布脚本相同的 Chrome Profile，便于在「草稿箱」里点发布。"""
    _ensure_patchright()
    from patchright.async_api import async_playwright

    account = account or _env(ACCOUNT_ENV, "main")
    profile = profile_dir(account=account)
    print(DRAFT_HINT, flush=True)
    print(f"浏览器配置: {profile.resolve()}", flush=True)
    print(
        "提示: 窗口已取消固定视口并尽量最大化；若仍滚不到底部，"
        " 请在编辑区空白处用触控板/滚轮滚动，或点「草稿箱→图文笔记→编辑」进入草稿。",
        flush=True,
    )
    print("关闭本终端或按 Ctrl+C 结束…", flush=True)

    async with async_playwright() as p:
        context, _ = await _launch_context(p, headless=False, account=account)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(
                NOTE_PUBLISH_URL,
                wait_until="domcontentloaded",
                timeout=90_000,
            )
            await asyncio.sleep(3600 * 24)
        except asyncio.CancelledError:
            pass
        finally:
            await context.close()


async def publish_forum_pack(
    pack_dir: Path,
    *,
    headless: bool | None = None,
    account: str | None = None,
    script: dict | None = None,
    force: bool = False,
) -> dict:
    """上传配图并填写标题/正文，点「暂存离开」（不点发布）。已有同标题草稿时默认跳过。"""
    _ensure_patchright()
    from patchright.async_api import async_playwright

    pack_dir = Path(pack_dir).resolve()
    data = parse_forum_pack(pack_dir)
    image_paths = _collect_images(pack_dir, data)
    title = _note_title(data, script)
    body = _forum_body_for_note(data)
    tags_line = _note_tags_line()

    if headless is None:
        headless = _headless()

    async with async_playwright() as p:
        context, cookie = await _launch_context(
            p, headless=headless, account=account
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            account = account or _env(ACCOUNT_ENV, "main")
            profile_rel = str(profile_dir(account=account).resolve())

            # 仅以浏览器草稿箱为准（不用 logs 跳过，避免草稿已删仍跳过、或检测失败反复新建）
            if not force and await _draft_row_has_title(page, title):
                draft_count = await _image_draft_count_in_modal(page)
                await context.storage_state(path=str(cookie))
                return {
                    "title": title,
                    "original_title": data["title"],
                    "pack_dir": data["pack_dir"],
                    "draft_only": True,
                    "published": False,
                    "skipped": True,
                    "editor_mode": "existing",
                    "images": image_paths,
                    "url": PUBLISH_HOME,
                    "draft_hint": DRAFT_HINT,
                    "browser_profile": profile_rel,
                    "image_draft_count": max(draft_count, 0),
                    "message": "草稿箱已有同标题图文草稿，跳过（--force 可在原稿上覆盖更新）",
                }

            mode = "new"
            if await _open_draft_by_title(page, title):
                mode = "draft"
            else:
                await _upload_images(page, image_paths)

            await _fill_title(page, title)
            await _fill_desc(page, body, tags_line)
            await _click_save_draft(page)
            draft_count = await _image_draft_count_in_modal(page)
            if draft_count < 0:
                raise XhsArticlePublishError(
                    "无法打开「草稿箱」校验。请 ./scripts/publish-xhs-article.sh <包> --headed"
                )
            if draft_count == 0:
                raise XhsArticlePublishError(
                    "已点「暂存草稿」但草稿箱里图文笔记仍为 0。"
                    " 请用 --headed 重试，或运行 ./xhs-open-creator.sh 在同一浏览器中手动暂存"
                )

            await context.storage_state(path=str(cookie))
            return {
                "title": title,
                "original_title": data["title"],
                "pack_dir": data["pack_dir"],
                "draft_only": True,
                "published": False,
                "skipped": False,
                "editor_mode": mode,
                "images": image_paths,
                "url": PUBLISH_HOME,
                "draft_hint": DRAFT_HINT,
                "browser_profile": profile_rel,
                "image_draft_count": draft_count,
            }
        finally:
            await context.close()
