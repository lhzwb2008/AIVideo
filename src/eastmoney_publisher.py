"""东方财富创作平台 · 长文图文发布（Playwright）。"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

from paths import ROOT


class EastmoneyPublishError(RuntimeError):
    pass


EDITOR_URL = "https://mp.eastmoney.com/collect/pc_article/index.html#/"
ACCOUNT_ENV = "EASTMONEY_ACCOUNT"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def sau_home(root: Path | None = None) -> Path:
    root = root or ROOT
    custom = _env("SAU_HOME")
    if custom:
        return Path(custom).expanduser()
    return root / "vendor" / "social-auto-upload"


def cookie_path(root: Path | None = None, account: str | None = None) -> Path:
    account = account or _env(ACCOUNT_ENV, "main")
    path = sau_home(root) / "cookies" / f"eastmoney_{account}.json"
    if not path.is_file():
        raise EastmoneyPublishError(
            f"未找到 cookie: {path}\n请先运行: ./eastmoney-login.sh"
        )
    return path


def profile_dir(root: Path | None = None, account: str | None = None) -> Path:
    account = account or _env(ACCOUNT_ENV, "main")
    return sau_home(root) / "cookies" / "browser_profiles" / f"eastmoney_{account}"


def _chrome_path() -> str:
    for path in (
        _env("LOCAL_CHROME_PATH"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    ):
        if path and Path(path).is_file():
            return path
    return ""


def _ensure_patchright():
    home = sau_home()
    venv_site = home / ".venv" / "lib"
    if venv_site.is_dir():
        for sub in venv_site.iterdir():
            if sub.name.startswith("python"):
                sys.path.insert(0, str(sub / "site-packages"))
                break
    try:
        from patchright.async_api import async_playwright  # noqa: F401
    except ImportError as exc:
        raise EastmoneyPublishError(
            "未安装 patchright（或当前 Python 与 SAU 环境不兼容）。"
            "请先运行: ./setup-sau.sh"
        ) from exc


def parse_forum_pack(pack_dir: Path) -> dict:
    post_md = pack_dir / "post.md"
    if not post_md.is_file():
        raise EastmoneyPublishError(f"缺少 post.md: {post_md}")

    raw = post_md.read_text(encoding="utf-8")
    lines = raw.splitlines()
    title = ""
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()

    sections: list[dict] = []
    disclaimer_lines: list[str] = []
    current_head = ""
    current_paras: list[str] = []
    pending_image: str | None = None
    in_footer = False

    def flush_section() -> None:
        nonlocal current_head, current_paras, pending_image
        body = "\n\n".join(p for p in current_paras if p.strip())
        if body or pending_image or current_head:
            sections.append(
                {
                    "headline": current_head,
                    "body": body.strip(),
                    "image": pending_image,
                }
            )
        current_head = ""
        current_paras = []
        pending_image = None

    for line in lines[1:]:
        s = line.strip()
        if s.startswith("---"):
            flush_section()
            in_footer = True
            continue
        if in_footer:
            if s:
                disclaimer_lines.append(s)
            continue
        if s.startswith("## "):
            flush_section()
            current_head = s[3:].strip()
            continue
        if s.startswith("【风险提示】"):
            disclaimer_lines.append(s)
            continue
        m = re.match(r"\*\*【插入配图\s*(\d+)】\*\*\s*`([^`]+)`", s)
        if m:
            rel = m.group(2).strip()
            img = pack_dir / rel
            if not img.is_file():
                raise EastmoneyPublishError(f"配图不存在: {img}")
            pending_image = str(img.resolve())
            continue
        if s.startswith("**【插入配图") or s.startswith("【插入配图"):
            continue
        if not s:
            continue
        current_paras.append(s)

    flush_section()
    disclaimer = "\n".join(disclaimer_lines).strip()

    cover = pack_dir / "cover.jpg"
    if not cover.is_file():
        raise EastmoneyPublishError(f"缺少 cover.jpg: {cover}")

    if not title:
        raise EastmoneyPublishError("post.md 缺少 # 标题行")
    if not sections:
        raise EastmoneyPublishError("post.md 无正文段落")

    return {
        "title": title,
        "sections": sections,
        "disclaimer": disclaimer,
        "cover": str(cover.resolve()),
        "pack_dir": str(pack_dir.resolve()),
    }


async def _open_longform_editor(page) -> None:
    await page.goto(EDITOR_URL, wait_until="domcontentloaded", timeout=90_000)
    title_input = page.locator('input[placeholder*="标题"]')
    if await title_input.count():
        return
    # 创作平台首页 → 侧边栏「长文」
    sub = page.locator(".sub1").first
    if await sub.count():
        await sub.click(timeout=15_000)
    else:
        await page.evaluate(
            "() => { const el = document.querySelector('.sub1'); if (el) el.click(); }"
        )
    await title_input.first.wait_for(state="visible", timeout=60_000)


async def _wait_logged_in(page, *, timeout_s: float = 120) -> None:
    for _ in range(int(timeout_s)):
        url = page.url.lower()
        if "usercenter" in url or "/login" in url:
            await asyncio.sleep(1)
            continue
        if page.locator('input[placeholder*="标题"]').count():
            return
        await asyncio.sleep(1)
    raise EastmoneyPublishError("等待登录/编辑器超时，请先 ./eastmoney-login.sh")


async def _fill_title(page, title: str) -> None:
    inp = page.locator('input[placeholder*="标题"]').first
    await inp.wait_for(state="visible", timeout=30_000)
    await inp.fill(title)


async def _focus_editor_end(page) -> None:
    editor = page.locator(".ProseMirror").first
    await editor.wait_for(state="visible", timeout=30_000)
    await editor.click(timeout=10_000)
    await page.keyboard.press("Control+End")


async def _fill_body_sections(
    page,
    sections: list[dict],
    *,
    disclaimer: str = "",
    insert_image=None,
) -> None:
    insert_image = insert_image or _insert_body_image
    editor = page.locator(".ProseMirror").first
    await editor.wait_for(state="visible", timeout=30_000)
    await editor.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")

    wrote = False
    for sec in sections:
        headline = (sec.get("headline") or "").strip()
        body = (sec.get("body") or "").strip()
        chunks = [c for c in (headline, body) if c]
        if chunks:
            await _focus_editor_end(page)
            if wrote:
                await page.keyboard.press("Enter")
                await page.keyboard.press("Enter")
            await page.keyboard.insert_text("\n\n".join(chunks))
            wrote = True
        img = sec.get("image")
        if img:
            await insert_image(page, img)
            wrote = True

    if disclaimer.strip():
        await _focus_editor_end(page)
        if wrote:
            await page.keyboard.press("Enter")
            await page.keyboard.press("Enter")
        await page.keyboard.insert_text(disclaimer.strip())
    await asyncio.sleep(0.5)


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

    cookie = cookie_path(account=account)
    profile = profile_dir(account=account)
    profile.mkdir(parents=True, exist_ok=True)

    # 优先用 storage_state（含 HttpOnly 外的登录 cookie），避免 profile 里只有匿名态
    if cookie.is_file() and cookie.stat().st_size > 64:
        browser = await p.chromium.launch(**launch)
        context = await browser.new_context(
            storage_state=str(cookie),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 1000},
        )
        return context, cookie

    if profile.is_dir() and any(profile.iterdir()):
        context = await p.chromium.launch_persistent_context(
            str(profile),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 1000},
            **launch,
        )
        return context, cookie

    browser = await p.chromium.launch(**launch)
    context = await browser.new_context(
        storage_state=str(cookie) if cookie.is_file() else None,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        viewport={"width": 1440, "height": 1000},
    )
    return context, cookie


async def _insert_body_image(page, image_path: str) -> None:
    if "usercenter" in page.url.lower() or "/login" in page.url.lower():
        raise EastmoneyPublishError("插入配图时跳转到登录页，请重新 ./eastmoney-login.sh")

    await _focus_editor_end(page)
    await page.keyboard.press("Enter")
    await page.keyboard.press("Enter")

    btn = page.locator("button.em_icon_image, .em_icon_image").first
    await btn.wait_for(state="visible", timeout=10_000)
    before = await page.locator(".ProseMirror img").count()

    await btn.click(timeout=10_000)
    await asyncio.sleep(0.5)
    file_input = page.locator(".upload_wrap #upload_input, .upload_wrap input[type='file']").first
    await file_input.wait_for(state="attached", timeout=15_000)
    await file_input.set_input_files(image_path)

    insert_btn = page.locator(".upload_wrap .btn_confirm").first
    for _ in range(90):
        if "usercenter" in page.url.lower():
            raise EastmoneyPublishError("上传配图后跳转到登录页")
        cls = await insert_btn.get_attribute("class") or ""
        if "disabled" not in cls:
            break
        await asyncio.sleep(1)
    else:
        raise EastmoneyPublishError(f"正文图片上传超时: {image_path}")

    await insert_btn.click(timeout=10_000)

    for _ in range(30):
        after = await page.locator(".ProseMirror img").count()
        if after > before:
            await asyncio.sleep(0.5)
            return
        await asyncio.sleep(1)
    raise EastmoneyPublishError(f"正文图片插入失败: {image_path}")


async def _dismiss_draft_banner(page) -> None:
    """忽略「未编辑草稿」提示，使用当前自动化填写的新稿。"""
    banner = page.get_by_text("是否继续编辑", exact=False)
    if await banner.count():
        close = page.locator(".el-message-box__close, .draft-tip-close, .close").first
        if await close.count():
            await close.click(timeout=3000)
        else:
            await page.keyboard.press("Escape")


async def _cover_is_set(page) -> bool:
    if await page.locator(".cover_edit_replace").count():
        return True
    if await page.locator(".cover_img_wrap img, .cover_img_part img").count():
        return True
    part = page.locator(".cover_img_part").first
    if await part.count():
        text = await part.inner_text()
        if "替换" in text or "编辑" in text:
            return True
    return False


async def _upload_cover(page, cover_path: str) -> None:
    if await _cover_is_set(page):
        return
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(0.5)
    cover = page.locator(".select-cover-img").first
    await cover.wait_for(state="visible", timeout=15_000)
    await cover.scroll_into_view_if_needed()
    await cover.click(timeout=10_000)
    inp = page.locator("#upload_input")
    await inp.wait_for(state="attached", timeout=15_000)
    await inp.set_input_files(cover_path)
    for _ in range(45):
        if await _cover_is_set(page):
            await asyncio.sleep(1)
            return
        await asyncio.sleep(1)
    raise EastmoneyPublishError("封面上传后未检测到预览图")


async def _set_source_personal(page) -> None:
    radio = page.locator(".el-radio").filter(has_text="个人观点").first
    if await radio.count():
        await radio.scroll_into_view_if_needed()
        await radio.click(timeout=5000)
        return
    loc = page.get_by_text("个人观点", exact=True)
    if await loc.count():
        await loc.first.click(timeout=3000)


async def _dismiss_dialogs(page) -> None:
    for _ in range(3):
        btn = page.locator(
            ".dialog_wrapper .btn_confirm, .dialog_wrapper button, "
            ".el-message-box__btns .el-button--primary"
        ).filter(has_text="确定")
        if await btn.count():
            await btn.first.click(timeout=3000, force=True)
            await asyncio.sleep(0.5)
            continue
        close = page.locator(".dialog_wrapper .close, .el-dialog__close").first
        if await close.count():
            await close.click(timeout=2000, force=True)
            await asyncio.sleep(0.5)
            continue
        break


async def _agree_terms(page) -> None:
    await _dismiss_dialogs(page)
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    icon = page.locator("footer.read_item .check-icon, .read_item .check-icon").first
    if await icon.count():
        cls = await icon.get_attribute("class") or ""
        if "on" not in cls.split():
            await icon.click(timeout=5000, force=True)
            await asyncio.sleep(0.3)
        cls = await icon.get_attribute("class") or ""
        if "on" not in cls.split():
            await page.evaluate(
                """() => {
                  const el = document.querySelector('footer.read_item .check-icon');
                  if (el && !el.classList.contains('on')) el.click();
                }"""
            )


async def _click_publish(page) -> None:
    await _dismiss_dialogs(page)
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await _set_source_personal(page)
    await _agree_terms(page)
    await asyncio.sleep(0.5)
    pub = page.locator(".button_publish, .editor-main-btn").filter(has_text="发布").first
    if not await pub.count():
        pub = page.get_by_text("发布", exact=True).last
    await pub.scroll_into_view_if_needed()
    await pub.click(timeout=15_000, force=True)
    for _ in range(20):
        await asyncio.sleep(1)
        url = page.url.lower()
        if "articlelist" in url or "success" in url:
            return
        if await page.get_by_text("发布成功", exact=False).count():
            return
        if await page.get_by_text("提交成功", exact=False).count():
            return
        if await page.get_by_text("发布文章成功", exact=False).count():
            view = page.get_by_text("查看我的文章", exact=False)
            if await view.count():
                await view.first.click(timeout=5000, force=True)
            return
        warn = page.get_by_text("请同意", exact=False)
        if await warn.count():
            await _dismiss_dialogs(page)
            await _agree_terms(page)
            await pub.click(timeout=10_000, force=True)
            continue
        confirm = page.locator(
            ".dialog_wrapper .btn_confirm, .el-message-box__btns .el-button--primary"
        ).filter(has_text="确定")
        if await confirm.count():
            await confirm.first.click(timeout=3000, force=True)
            continue
    await asyncio.sleep(2)


async def publish_forum_pack(
    pack_dir: Path,
    *,
    headless: bool = True,
    draft_only: bool = True,
    account: str | None = None,
) -> dict:
    _ensure_patchright()
    from patchright.async_api import async_playwright

    data = parse_forum_pack(pack_dir)
    try:
        cookie = cookie_path(account=account)
    except EastmoneyPublishError:
        cookie = sau_home() / "cookies" / f"eastmoney_{account or 'main'}.json"

    async with async_playwright() as p:
        context, cookie = await _launch_context(
            p, headless=headless, account=account
        )
        close_browser = not hasattr(context, "pages")
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await _open_longform_editor(page)
            url = page.url.lower()
            if "usercenter" in url or "login" in url:
                raise EastmoneyPublishError("未登录，请先 ./eastmoney-login.sh")

            await _dismiss_draft_banner(page)
            await _fill_title(page, data["title"])
            await _upload_cover(page, data["cover"])
            await _fill_body_sections(
                page, data["sections"], disclaimer=data.get("disclaimer") or ""
            )
            await _set_source_personal(page)
            await _agree_terms(page)

            await asyncio.sleep(2)
            if draft_only:
                preview = page.get_by_text("保存并预览", exact=False).first
                if await preview.count():
                    await preview.click(timeout=10_000)
                    await asyncio.sleep(2)
            else:
                await _click_publish(page)

            await context.storage_state(path=str(cookie))
            return {
                "title": data["title"],
                "pack_dir": data["pack_dir"],
                "cover": data["cover"],
                "images": [s.get("image") for s in data["sections"] if s.get("image")],
                "draft_only": draft_only,
                "url": page.url,
            }
        finally:
            await context.close()
