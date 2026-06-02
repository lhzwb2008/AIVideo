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
            "未安装 patchright。请先运行: ./scripts/setup-sau.sh"
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
    current_head = ""
    current_paras: list[str] = []
    pending_image: str | None = None

    def flush_section() -> None:
        nonlocal current_head, current_paras, pending_image
        body = "\n\n".join(p for p in current_paras if p.strip())
        if body or pending_image:
            sections.append(
                {
                    "headline": current_head,
                    "body": body.strip(),
                    "image": pending_image,
                }
            )
        current_paras = []
        pending_image = None

    for line in lines[1:]:
        s = line.strip()
        if s.startswith("## "):
            flush_section()
            current_head = s[3:].strip()
            continue
        if s.startswith("---"):
            continue
        if s.startswith("【风险提示】"):
            current_paras.append(s)
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


async def _fill_body_sections(page, sections: list[dict]) -> None:
    editor = page.locator(".ProseMirror").first
    await editor.wait_for(state="visible", timeout=30_000)
    await editor.click()
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")

    wrote = False
    for sec in sections:
        body = (sec.get("body") or "").strip()
        if body:
            if wrote:
                await page.keyboard.press("Enter")
                await page.keyboard.press("Enter")
            await page.keyboard.insert_text(body)
            wrote = True
        img = sec.get("image")
        if img:
            await _insert_body_image(page, img)
            wrote = True
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

    profile = profile_dir(account=account)
    profile.mkdir(parents=True, exist_ok=True)
    cookie = cookie_path(account=account)

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

    editor = page.locator(".ProseMirror").first
    await editor.wait_for(state="visible", timeout=15_000)
    await editor.click(timeout=10_000)
    await page.keyboard.press("Control+End")
    await page.keyboard.press("Enter")
    await page.keyboard.press("Enter")

    btn = page.locator("button.em_icon_image, .em_icon_image").first
    await btn.wait_for(state="visible", timeout=10_000)
    before = await page.locator(".ProseMirror img").count()

    async with page.expect_file_chooser(timeout=15_000) as fc_info:
        await btn.click(timeout=10_000)
    chooser = await fc_info.value
    await chooser.set_files(image_path)

    for _ in range(90):
        if "usercenter" in page.url.lower():
            raise EastmoneyPublishError("上传配图后跳转到登录页")
        after = await page.locator(".ProseMirror img").count()
        if after > before:
            await asyncio.sleep(1.5)
            return
        await asyncio.sleep(1)
    raise EastmoneyPublishError(f"正文图片上传失败: {image_path}")


async def _upload_cover(page, cover_path: str) -> None:
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await asyncio.sleep(0.5)
    await page.locator(".select-cover-img").first.scroll_into_view_if_needed()
    await page.locator(".select-cover-img").first.click(timeout=10_000)
    inp = page.locator("#upload_input")
    await inp.wait_for(state="attached", timeout=15_000)
    await inp.set_input_files(cover_path)
    await asyncio.sleep(2)
    wrap = page.locator(".cover_img_wrap img, .cover_img_part img")
    for _ in range(30):
        if await wrap.count():
            return
        await asyncio.sleep(1)
    raise EastmoneyPublishError("封面上传后未检测到预览图")


async def _set_source_personal(page) -> None:
    for text in ("个人观点",):
        loc = page.get_by_text(text, exact=True)
        if await loc.count():
            await loc.first.click(timeout=3000)
            return


async def _agree_terms(page) -> None:
    label = page.get_by_text("已阅读并同意", exact=False)
    if await label.count():
        await label.first.click(timeout=5000)
        return
    cb = page.locator(".el-checkbox").first
    if await cb.count():
        await cb.click(timeout=5000)


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

            await _fill_title(page, data["title"])
            await _upload_cover(page, data["cover"])
            await _fill_body_sections(page, data["sections"])
            await _set_source_personal(page)
            await _agree_terms(page)

            await asyncio.sleep(2)
            if draft_only:
                preview = page.get_by_text("保存并预览", exact=False).first
                if await preview.count():
                    await preview.click(timeout=10_000)
                    await asyncio.sleep(2)
            else:
                pub = page.get_by_role("button", name="发布", exact=True)
                await pub.click(timeout=15_000)
                await asyncio.sleep(3)

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
