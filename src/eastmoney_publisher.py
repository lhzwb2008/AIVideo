"""东方财富创作平台 · 长文图文发布（Playwright）。"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

from forum_editor_fill import (
    dedupe_body_paragraphs,
    fill_eastmoney_body_sections,
    focus_editor_end,
    move_cursor_to_end,
    prepare_image_upload,
)
from forum_pack_format import extract_caption, is_caption_line, join_forum_paragraphs
from paths import ROOT


class EastmoneyPublishError(RuntimeError):
    pass


EDITOR_URL = "https://mp.eastmoney.com/collect/pc_article/index.html#/"
ACCOUNT_ENV = "EASTMONEY_ACCOUNT"
DEFAULT_TOPIC_ENV = "EASTMONEY_DEFAULT_TOPIC"
DEFAULT_TOPIC_NAME = "社区牛人计划"


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


def account_label_path(root: Path | None = None, account: str | None = None) -> Path:
    account = account or _env(ACCOUNT_ENV, "main")
    return sau_home(root) / "cookies" / f"eastmoney_{account}.account"


def read_saved_account_label(root: Path | None = None, account: str | None = None) -> str:
    path = account_label_path(root, account)
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


def save_account_label(label: str, root: Path | None = None, account: str | None = None) -> None:
    label = label.strip()
    if not label:
        return
    path = account_label_path(root, account)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(label, encoding="utf-8")


async def read_editor_account_label(page) -> str:
    return (
        await page.evaluate(
            """
            () => {
              const selectors = [
                '.user-name', '.nick-name', '.nickname', '[class*="nick"]',
                '[class*="user-name"]', '.header-user', '.author-name',
              ];
              for (const sel of selectors) {
                for (const el of document.querySelectorAll(sel)) {
                  const t = (el.textContent || '').trim();
                  if (t && t.length >= 2 && t.length <= 32) return t;
                }
              }
              return '';
            }
            """
        )
        or ""
    ).strip()


async def ensure_expected_account(page, *, account: str | None = None) -> str:
    label = await read_editor_account_label(page)
    if label:
        save_account_label(label, account=account)
        print(f"  东方财富账号: {label}", flush=True)
        return label
    saved = read_saved_account_label(account=account)
    if saved:
        print(f"  东方财富账号: {saved}（沿用上次登录记录）", flush=True)
    return saved


def profile_dir(root: Path | None = None, account: str | None = None) -> Path:
    account = account or _env(ACCOUNT_ENV, "main")
    return sau_home(root) / "cookies" / "browser_profiles" / f"eastmoney_{account}"


async def _grant_clipboard(context) -> None:
    try:
        await context.grant_permissions(["clipboard-read", "clipboard-write"])
    except Exception:
        pass


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
    pending_caption: str = ""
    in_footer = False

    def flush_section() -> None:
        nonlocal current_head, current_paras, pending_image, pending_caption
        body = join_forum_paragraphs(dedupe_body_paragraphs(current_paras))
        if body or pending_image or current_head or pending_caption:
            sec: dict = {
                "headline": current_head,
                "body": body.strip(),
                "image": pending_image,
            }
            if pending_caption:
                sec["caption"] = pending_caption
            sections.append(sec)
        current_head = ""
        current_paras = []
        pending_image = None
        pending_caption = ""

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
        if not m:
            m = re.match(r"\*\*【插入配图\s*(\d+)】\*\*\s+(\S+)", s)
        if m:
            rel = m.group(2).strip().strip("`")
            img = pack_dir / rel
            if not img.is_file():
                raise EastmoneyPublishError(f"配图不存在: {img}")
            pending_image = str(img.resolve())
            continue
        if s.startswith("**【插入配图") or s.startswith("【插入配图"):
            continue
        if is_caption_line(s):
            pending_caption = extract_caption(s)
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


def distinct_eastmoney_title(title: str, pack_dir: Path) -> str:
    """重发时略改标题，避免东方财富「标题重复」拦截（不用特殊符号/重发字样）。"""
    tweaks = [
        ("为什么", "为何"),
        ("为何", "为什么"),
        ("突然", "忽然"),
        ("为啥", "为什么"),
        ("怎么走", "如何走"),
        ("逆市", "逆势"),
        ("开始", "着手"),
        ("全线", "整体"),
        ("板块", "赛道"),
    ]
    start = sum(ord(c) for c in pack_dir.name) % len(tweaks)
    for i in range(len(tweaks)):
        src, dst = tweaks[(start + i) % len(tweaks)]
        if src in title:
            candidate = title.replace(src, dst, 1)
            if candidate != title:
                return candidate[:100]
    # 最后兜底：换一字，不用括号/符号
    if "？" in title:
        return title.replace("？", "吗？", 1)[:100]
    return title[:100]


async def _read_dialog_text(page) -> str:
    try:
        return (
            await page.evaluate(
                """
                () => Array.from(
                  document.querySelectorAll(
                    '.dialog_wrapper, .dialog, [class*="dialog"], .el-message-box'
                  )
                )
                  .map(el => (el.innerText || '').trim())
                  .filter(Boolean)
                  .join('\\n')
                """
            )
            or ""
        )
    except Exception:
        return ""


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
    await inp.click()
    await inp.fill(title)
    await asyncio.sleep(0.8)
    url = page.url.lower()
    if "usercenter" in url or "/login" in url:
        raise EastmoneyPublishError(
            "登录态失效（填标题后跳转到 usercenter/login）。"
            "请运行: ./eastmoney-login.sh --force"
        )
    editor = page.locator(".ProseMirror.cfh_editor_area, .ProseMirror").first
    for _ in range(40):
        if "usercenter" in page.url.lower():
            raise EastmoneyPublishError(
                "登录态失效，请运行: ./eastmoney-login.sh --force"
            )
        if await page.locator(".ProseMirror.cfh_editor_area, .ProseMirror").count():
            try:
                if await editor.is_visible():
                    break
            except Exception:
                pass
        await asyncio.sleep(0.5)
    else:
        raise EastmoneyPublishError(
            f"填标题后正文编辑器未出现（{page.url}）。"
            "请运行: ./eastmoney-login.sh --force"
        )
    await asyncio.sleep(0.3)


async def _focus_editor_end(page) -> None:
    await focus_editor_end(page)


async def _fill_body_sections(
    page,
    sections: list[dict],
    *,
    pack_dir: Path,
    disclaimer: str = "",
    insert_image=None,
    cover_image: str | None = None,
) -> None:
    await fill_eastmoney_body_sections(
        page,
        sections,
        pack_dir=pack_dir,
        disclaimer=disclaimer,
        insert_image=insert_image or _insert_body_image,
        cover_image=cover_image,
    )


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

    # 有头调试优先 profile；日常 headless 用已同步的 storage_state（与 login 同账号）
    if not headless and profile.is_dir() and any(profile.iterdir()):
        context = await p.chromium.launch_persistent_context(
            str(profile),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 1000},
            **launch,
        )
        await _grant_clipboard(context)
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

    if profile.is_dir() and any(profile.iterdir()):
        context = await p.chromium.launch_persistent_context(
            str(profile),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 1000},
            **launch,
        )
        await _grant_clipboard(context)
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


async def _ensure_editor_page(page) -> None:
    if "pc_article" not in page.url.lower():
        await _open_longform_editor(page)
    await page.locator('input[placeholder*="标题"]').first.wait_for(
        state="visible", timeout=30_000
    )
    await page.locator(".ProseMirror.cfh_editor_area, .ProseMirror").first.wait_for(
        state="attached", timeout=30_000
    )
    await page.evaluate("window.scrollTo(0, 0)")
    await asyncio.sleep(0.3)


async def _insert_body_image(page, image_path: str) -> None:
    if "usercenter" in page.url.lower() or "/login" in page.url.lower():
        raise EastmoneyPublishError("插入配图时跳转到登录页，请重新 ./eastmoney-login.sh")

    await _dismiss_dialogs(page)
    await move_cursor_to_end(page)
    await page.keyboard.press("Enter")

    btn = page.locator("button.em_icon_image, .em_icon_image").first
    await btn.wait_for(state="visible", timeout=10_000)
    before = await page.locator(".ProseMirror img").count()

    await _dismiss_dialogs(page)
    try:
        await btn.click(timeout=10_000)
    except Exception:
        await _hide_prompt_overlays(page)
        await btn.click(timeout=10_000, force=True)
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
            await move_cursor_to_end(page)
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


def _default_topic_name() -> str:
    raw = _env(DEFAULT_TOPIC_ENV, DEFAULT_TOPIC_NAME)
    if not raw:
        return ""
    name = raw.strip().strip("#")
    return name


async def _editor_has_topic_module(page, topic: str) -> bool:
    if not topic:
        return False
    try:
        return bool(
            await page.evaluate(
                """
                (topic) => Array.from(
                  document.querySelectorAll('.module.module_topic')
                ).some((el) => (el.innerText || '').includes(topic))
                """,
                topic,
            )
        )
    except Exception:
        return False


async def _move_topic_module_to_top(page) -> bool:
    """把已插入的 module_topic 段落挪到正文最上方（话题栏）。"""
    try:
        return bool(
            await page.evaluate(
                """
                () => {
                  const root = document.querySelector('.ProseMirror.cfh_editor_area')
                    || document.querySelector('.ProseMirror');
                  if (!root) return false;
                  const topic = root.querySelector('.module.module_topic');
                  if (!topic) return false;
                  const hostP = topic.closest('p');
                  if (!hostP || hostP === root.firstElementChild) return true;
                  const slot = hostP.cloneNode(true);
                  hostP.remove();
                  root.insertBefore(slot, root.firstChild);
                  return true;
                }
                """
            )
        )
    except Exception:
        return False


async def _select_topic_from_panel(page, topic: str) -> bool:
    # 光标在正文内时话题面板常不出现；先点到标题栏再开「话题」。
    title = page.locator('input[placeholder*="标题"]').first
    await title.click(timeout=5000)
    await asyncio.sleep(0.2)

    btn = page.locator("button.em_icon_topic, .em_icon_topic").first
    await btn.wait_for(state="visible", timeout=10_000)
    await btn.click(timeout=10_000)
    await asyncio.sleep(0.8)

    panel = page.locator(".mention_suggest.topic_text").first
    try:
        await panel.wait_for(state="visible", timeout=10_000)
    except Exception:
        return False

    activity_tab = panel.get_by_text("活动", exact=True).first
    if await activity_tab.count():
        await activity_tab.click(timeout=5000)
        await asyncio.sleep(0.8)

    item = panel.get_by_text(topic, exact=True).first
    if not await item.count():
        await page.keyboard.type(topic)
        await asyncio.sleep(1.2)
        item = panel.get_by_text(topic, exact=True).first

    if not await item.count():
        await page.keyboard.press("Escape")
        return False

    await item.click(timeout=5000)
    await asyncio.sleep(0.5)
    return True


async def _add_default_topic(page) -> None:
    """长文发布：在话题栏（正文上方独立 module_topic）挂上活动话题。"""
    topic = _default_topic_name()
    if not topic:
        return
    if await _editor_has_topic_module(page, topic):
        return

    if not await _select_topic_from_panel(page, topic):
        print(f"  东方财富话题栏: 未能选择 #{topic}#", flush=True)
        return

    if not await _move_topic_module_to_top(page):
        print(f"  东方财富话题栏: 已选 #{topic}#（未能移到顶部）", flush=True)
        return

    print(f"  东方财富话题栏: #{topic}#", flush=True)


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


async def _hide_prompt_overlays(page) -> bool:
    """prompt_wrapper 会挡住 em_icon_image 等工具栏；JS 兜底移除。"""
    try:
        removed = await page.evaluate(
            """
            () => {
              let n = 0;
              document.querySelectorAll('.prompt_wrapper').forEach((el) => {
                el.style.display = 'none';
                el.style.pointerEvents = 'none';
                el.remove();
                n += 1;
              });
              return n;
            }
            """
        )
        return bool(removed)
    except Exception:
        return False


async def _dismiss_prompt_wrapper(page) -> bool:
    """关闭东方财富编辑器新手引导/提示层（常见遮挡插图按钮）。"""
    dismissed = False
    prompt = page.locator(".prompt_wrapper").first
    try:
        if not await prompt.is_visible(timeout=400):
            return False
    except Exception:
        return False

    for sel in (
        ".btn_confirm",
        ".dialog_btn_confirm",
        "button:has-text('知道了')",
        "button:has-text('我知道了')",
        "button:has-text('确定')",
        "button:has-text('关闭')",
        "button",
    ):
        btn = prompt.locator(sel).first
        try:
            if await btn.is_visible(timeout=300):
                await btn.click(timeout=3000, force=True)
                await asyncio.sleep(0.4)
                dismissed = True
                break
        except Exception:
            continue

    if not dismissed:
        for close_sel in (".close", ".el-dialog__close", "[class*='close']"):
            close = prompt.locator(close_sel).first
            try:
                if await close.is_visible(timeout=300):
                    await close.click(timeout=2000, force=True)
                    dismissed = True
                    await asyncio.sleep(0.3)
                    break
            except Exception:
                continue

    if not dismissed:
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.3)
        except Exception:
            pass

    if await prompt.is_visible(timeout=300):
        dismissed = await _hide_prompt_overlays(page) or dismissed
    return dismissed


async def _dismiss_dialogs(page) -> None:
    for _ in range(6):
        clicked = False
        if await _dismiss_prompt_wrapper(page):
            clicked = True
        for sel in (
            ".dialog_btn_confirm",
            ".dialog_wrapper .btn_confirm",
            ".el-message-box__btns .el-button--primary",
            ".prompt_wrapper .btn_confirm",
            ".prompt_wrapper .dialog_btn_confirm",
            ".prompt_wrapper button",
        ):
            btn = page.locator(sel).first
            try:
                if await btn.is_visible(timeout=300):
                    await btn.click(timeout=3000, force=True)
                    await asyncio.sleep(0.5)
                    clicked = True
                    break
            except Exception:
                continue
        if clicked:
            continue
        close = page.locator(".dialog_wrapper .close, .el-dialog__close").first
        try:
            if await close.is_visible(timeout=300):
                await close.click(timeout=2000, force=True)
                await asyncio.sleep(0.5)
                continue
        except Exception:
            pass
        if await _hide_prompt_overlays(page):
            continue
        break


async def _set_source_personal(page) -> None:
    await _dismiss_dialogs(page)
    radio = page.locator(".el-radio").filter(has_text="个人观点").first
    if await radio.count():
        await radio.scroll_into_view_if_needed()
        await radio.click(timeout=8000, force=True)
        return
    loc = page.get_by_text("个人观点", exact=True)
    if await loc.count():
        await loc.first.click(timeout=3000, force=True)


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


async def _publish_succeeded(page) -> bool:
    url = page.url.lower()
    if any(k in url for k in ("articlelist", "article/list", "success", "mycenter")):
        return True
    for text in (
        "发布成功",
        "提交成功",
        "发布文章成功",
        "已提交",
        "提交审核",
        "进入审核",
    ):
        if await page.get_by_text(text, exact=False).count():
            return True
    return False


async def _click_confirm_dialogs(page) -> None:
    for sel in (
        ".dialog_btn_confirm",
        ".dialog_wrapper .btn_confirm",
        ".el-message-box__btns .el-button--primary",
        ".el-dialog__footer .el-button--primary",
    ):
        btn = page.locator(sel).first
        try:
            if await btn.is_visible(timeout=800):
                await btn.click(timeout=3000, force=True)
                await asyncio.sleep(0.8)
        except Exception:
            continue


async def _click_publish(page) -> None:
    await _dismiss_dialogs(page)
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await _set_source_personal(page)
    await _agree_terms(page)
    await asyncio.sleep(0.5)

    pub = page.locator(".button_publish").first
    if not await pub.count():
        pub = page.locator(".editor-main-btn").filter(has_text="发布").first
    if not await pub.count():
        pub = page.get_by_role("button", name="发布").first
    await pub.wait_for(state="attached", timeout=15_000)
    await pub.scroll_into_view_if_needed()

    for attempt in range(3):
        await pub.click(timeout=15_000, force=True)
        await asyncio.sleep(1.2)
        dialog = await _read_dialog_text(page)
        if any(k in dialog for k in ("标题重复", "标题已存在", "请勿发布重复")):
            raise EastmoneyPublishError(f"标题重复：{dialog[:120]}")
        await _click_confirm_dialogs(page)
        dialog = await _read_dialog_text(page)
        if any(k in dialog for k in ("标题重复", "标题已存在", "请勿发布重复")):
            raise EastmoneyPublishError(f"标题重复：{dialog[:120]}")
        for _ in range(30):
            if await _publish_succeeded(page):
                return
            dialog = await _read_dialog_text(page)
            if any(k in dialog for k in ("标题重复", "标题已存在", "请勿发布重复")):
                raise EastmoneyPublishError(f"标题重复：{dialog[:120]}")
            body = await page.evaluate("() => document.body.innerText || ''")
            if any(k in body for k in ("发布成功", "提交成功", "发布文章成功")):
                return
            await _click_confirm_dialogs(page)
            await asyncio.sleep(1)
        if attempt < 2:
            await _agree_terms(page)

    raise EastmoneyPublishError(
        "点击发布后未检测到成功页，文章可能仍在草稿箱。"
        f" 当前 URL: {page.url}。"
        " 请到创作中心草稿箱确认后重试。"
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
            publish_title = distinct_eastmoney_title(data["title"], pack_dir)
            if publish_title != data["title"]:
                print(f"  标题略改（避免重复）: {publish_title}", flush=True)
            await _fill_title(page, publish_title)
            await _dismiss_dialogs(page)
            await _fill_body_sections(
                page,
                data["sections"],
                pack_dir=pack_dir,
                disclaimer=data.get("disclaimer") or "",
                cover_image=data.get("cover"),
            )
            await _add_default_topic(page)
            await _upload_cover(page, data["cover"])
            await ensure_expected_account(page, account=account)
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
                "title": publish_title,
                "original_title": data["title"],
                "pack_dir": data["pack_dir"],
                "cover": data["cover"],
                "images": [
                    data.get("cover"),
                    *[s.get("image") for s in data["sections"] if s.get("image")],
                ],
                "draft_only": draft_only,
                "url": page.url,
            }
        finally:
            await context.close()
