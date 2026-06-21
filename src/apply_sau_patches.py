#!/usr/bin/env python3
"""对 vendor/social-auto-upload 打 AIVideo 兼容补丁（可重复执行）。"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

from paths import ROOT

TARGET = ROOT / "vendor" / "social-auto-upload" / "uploader" / "douyin_uploader" / "main.py"


def _patch_block(text: str) -> str:
    """Strip surrounding newlines only — preserve indentation of indented patch blocks."""
    return text.strip("\n")

XHS_TARGET = ROOT / "vendor" / "social-auto-upload" / "uploader" / "xiaohongshu_uploader" / "main.py"
TENCENT_TARGET = ROOT / "vendor" / "social-auto-upload" / "uploader" / "tencent_uploader" / "main.py"
BILIUP_RUNTIME = ROOT / "vendor" / "social-auto-upload" / "uploader" / "bilibili_uploader" / "runtime.py"
BILIUP_GH_PROXY_MARKER = "AIVIDEO_PATCH: GH_PROXY for biliup download"

TENCENT_UPLOAD_MARKER = "AIVIDEO_PATCH: 跳过封面/短标题"
TENCENT_UPLOAD_OLD = """            await self.upload_video_file(page, self.file_path)
            await self.prepare_video_for_publish(page)
            await self.wait_for_upload_complete(page)
            await self.set_thumbnail(page)

            if self.publish_strategy == TENCENT_PUBLISH_STRATEGY_SCHEDULED and self.publish_date != 0:
                await self.set_schedule_time_tencent(page, self.publish_date)

            await self.set_short_title(page, self.title, self.short_title)
            await self.submit_publish(page)"""

TENCENT_UPLOAD_NEW = """            await self.upload_video_file(page, self.file_path)
            await self.wait_for_upload_complete(page)
            # AIVIDEO_PATCH: 跳过封面/短标题，仅填描述+原创后发表
            editor = page.locator("div.input-editor").first
            await editor.wait_for(state="visible", timeout=120000)
            await editor.click()
            body = (self.desc or "").strip()
            if body:
                await page.keyboard.type(body)
            await self.apply_original_statement(page)

            if self.publish_strategy == TENCENT_PUBLISH_STRATEGY_SCHEDULED and self.publish_date != 0:
                await self.set_schedule_time_tencent(page, self.publish_date)

            await self.submit_publish(page)"""

_XHS_FILL_TAGS_FUNC = re.compile(
    r"    async def fill_tags\(self, page: Page\) -> None:.*?(?=    async def fill_meta)",
    re.DOTALL,
)

# 小红书 fill_tags 容错版：弹不出官方话题下拉时，按空格把 #标签 作为普通文本提交，
# 不再因 TimeoutError 整个发布失败。
XHS_FILL_TAGS = '''    async def fill_tags(self, page: Page) -> None:
        if not getattr(self, "tags", None):
            return

        if not getattr(self, "desc", ""):
            desc = page.locator('p[data-placeholder*="输入正文描述"]')
            await desc.click()

        for tag in self.tags[:5]:  # AIVIDEO_PATCH: 最多 5 个话题，超出会被平台截断/乱码
            await page.keyboard.type("#" + tag, delay=30)
            try:
                await page.locator('#creator-editor-topic-container').wait_for(
                    state="visible",
                    timeout=3000,
                )
                first_item = page.locator('#creator-editor-topic-container .item').first
                await first_item.wait_for(state="visible", timeout=2000)
                await first_item.click()
            except Exception:
                # 没有官方话题联想（如品牌词），按空格把它当普通 #标签 文本提交
                await page.keyboard.press("Space")
            await page.wait_for_timeout(300)
'''

HELPER = '''
def _resolve_chrome_path() -> str:
    if LOCAL_CHROME_PATH:
        return LOCAL_CHROME_PATH
    for path in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
    ):
        if Path(path).is_file():
            return path
    return ""


def _build_launch_kwargs(headless: bool) -> dict:
    launch_kwargs = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--lang=zh-CN",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    }
    if not headless:
        launch_kwargs["args"].append("--start-maximized")
    chrome_path = _resolve_chrome_path()
    if chrome_path:
        launch_kwargs["executable_path"] = chrome_path
    else:
        launch_kwargs["channel"] = "chrome"
    return launch_kwargs


def _build_context_kwargs() -> dict:
    return {
        "locale": "zh-CN",
        "timezone_id": "Asia/Shanghai",
        "viewport": {"width": 1440, "height": 900},
    }


async def _douyin_goto(page, url: str):
    return await page.goto(url, wait_until="domcontentloaded", timeout=120_000)
'''

COOKIE_GEN_BLOCK = '''
        browser = None
        profile_dir = Path(account_file).parent / "browser_profiles" / Path(account_file).stem
        profile_dir.mkdir(parents=True, exist_ok=True)
        if not headless:
            context = await playwright.chromium.launch_persistent_context(
                str(profile_dir),
                **_build_launch_kwargs(headless=False),
                **_build_context_kwargs(),
            )
            context = await set_init_script(context)
        else:
            browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=headless))
            context = await browser.new_context(**_build_context_kwargs())
            context = await set_init_script(context)
        qrcode_path = None
        result = _build_login_result(False, "failed", "抖音登录失败", account_file)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
'''

WAIT_LOGIN_BUSY_CHECK = '''        # AIVIDEO_PATCH: douyin busy check
        busy_marker = page.get_by_text("系统繁忙").first
        if await busy_marker.count():
            try:
                if await busy_marker.is_visible():
                    douyin_logger.warning(_msg("😵", "检测到系统繁忙（抖音风控），刷新页面重试…"))
                    await page.reload(wait_until="domcontentloaded", timeout=120_000)
                    await asyncio.sleep(2)
                    qrcode_info = await _save_douyin_qrcode(page, account_file, qrcode_path, qrcode_callback=qrcode_callback)
                    qrcode_path = Path(qrcode_info["image_path"])
                    continue
            except Exception:
                pass

'''

DOUYIN_BUSY_MARKER = "AIVIDEO_PATCH: douyin busy check"

# 匹配任意缩进/CRLF 的 busy_marker 块（含旧版误插入的 orphan）
_STRIP_BUSY_MARKER = re.compile(
    r"\r?\n[ \t]*(?:# AIVIDEO_PATCH: douyin busy check\r?\n)?"
    r"[ \t]*busy_marker = page\.get_by_text\([\"']系统繁忙[\"']\)\.first\r?\n"
    r"(?:[ \t]*.*\r?\n)*?"
    r"[ \t]*except Exception(?: as \w+)?:\r?\n"
    r"[ \t]*pass\r?\n",
)


def _strip_all_busy_marker_blocks(text: str) -> str:
    prev = None
    while prev != text:
        prev = text
        text = _STRIP_BUSY_MARKER.sub("\n", text)
    return text


_DOUYIN_BUSY_INSERT = re.compile(
    r"(async def _wait_for_douyin_login\([^\)]*\)[^\n]*\r?\n.*?"
    r"    for _ in range\(max_checks\):\r?\n"
    r"        if await _is_douyin_login_completed\(page\):[^\n]*\r?\n"
    r"            douyin_logger\.info\([^\n]+\r?\n"
    r"            return _build_login_result\([^\n]+\r?\n\r?\n)"
    r'(        expired_box = page\.get_by_text\("二维码失效")',
    re.DOTALL,
)


def _restore_douyin_from_git(path: Path) -> bool:
    sau_root = path.parents[2]
    if not (sau_root / ".git").is_dir():
        return False
    rel = path.relative_to(sau_root).as_posix()
    proc = subprocess.run(
        ["git", "checkout", "--", rel],
        cwd=sau_root,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0 and path.is_file()


def _ensure_valid_douyin_main(path: Path) -> tuple[str, list[str]]:
    """语法损坏时先剥离误插入块，仍失败则从 SAU git 恢复 upstream。"""
    notes: list[str] = []
    original = path.read_text(encoding="utf-8")
    text = _strip_all_busy_marker_blocks(original)
    if text != original:
        notes.append("strip_orphan_busy")

    try:
        ast.parse(text)
        if notes:
            path.write_text(text, encoding="utf-8")
        return text, notes
    except SyntaxError:
        pass

    if _restore_douyin_from_git(path):
        notes.append("git_restore")
        text = path.read_text(encoding="utf-8")
        try:
            ast.parse(text)
            return text, notes
        except SyntaxError as exc:
            print(f"git 恢复后仍有语法错误: {exc}", file=sys.stderr)
            sys.exit(1)

    try:
        ast.parse(text)
        path.write_text(text, encoding="utf-8")
        return text, notes
    except SyntaxError as exc:
        print(f"抖音 main.py 无法自动修复: {exc}", file=sys.stderr)
        print(
            "请手动执行: .\\scripts\\repair-sau-douyin.ps1",
            file=sys.stderr,
        )
        sys.exit(1)


def _fix_douyin_busy_check(text: str) -> str:
    """Remove orphan busy_marker blocks, insert only inside _wait_for_douyin_login."""
    text = _strip_all_busy_marker_blocks(text)
    if DOUYIN_BUSY_MARKER in text:
        return text
    match = _DOUYIN_BUSY_INSERT.search(text)
    if not match:
        return text
    return _DOUYIN_BUSY_INSERT.sub(
        lambda m: m.group(1) + WAIT_LOGIN_BUSY_CHECK + "\n" + m.group(2),
        text,
        count=1,
    )

COOKIE_AUTH = '''
async def cookie_auth(account_file):
    for attempt in range(3):
        if await _cookie_auth_once(account_file):
            return True
        if attempt < 2:
            await asyncio.sleep(2)
    return False


async def _cookie_auth_once(account_file):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_build_launch_kwargs(headless=True))
        try:
            context = await browser.new_context(storage_state=account_file, **_build_context_kwargs())
            context = await set_init_script(context)
            page = await context.new_page()
            await _douyin_goto(page, "https://creator.douyin.com/creator-micro/content/upload")
            url = page.url.lower()
            if "passport" in url or "/login" in url:
                return False
            if not page.url.startswith("https://creator.douyin.com/creator-micro/"):
                return False
            for text in ("扫码登录", "手机号登录"):
                marker = page.get_by_text(text, exact=True).first
                if not await marker.count():
                    continue
                try:
                    if await marker.is_visible():
                        return False
                except Exception:
                    continue
            selectors = (
                "input.semi-upload-hidden-input",
                "input[type='file'][accept*='video']",
                "input[type='file']",
            )
            for _ in range(20):
                for sel in selectors:
                    if await page.locator(sel).first.count():
                        return True
                await asyncio.sleep(1)
            return False
        except Exception:
            return False
        finally:
            await browser.close()
'''


def patch(path: Path) -> None:
    if not path.is_file():
        print(f"跳过：未找到 {path}", file=sys.stderr)
        sys.exit(1)

    text, pre_notes = _ensure_valid_douyin_main(path)
    applied: list[str] = list(pre_notes)

    if "_build_launch_kwargs" not in text or "_douyin_goto" not in text:
        helper_pattern = (
            r"def _resolve_chrome_path\(\) -> str:.*?async def _douyin_goto\(page, url: str\):\n"
            r"    return await page\.goto\(url, wait_until=\"domcontentloaded\", timeout=120_000\)"
        )
        if re.search(helper_pattern, text, re.DOTALL):
            text = re.sub(helper_pattern, HELPER.strip(), text, count=1, flags=re.DOTALL)
            applied.append("helper")
        else:
            anchor = "async def cookie_auth(account_file):"
            if anchor not in text:
                print("补丁锚点缺失，请检查 upstream 是否变更", file=sys.stderr)
                sys.exit(1)
            text = text.replace(anchor, HELPER.strip() + "\n\n\n" + anchor)
            applied.append("helper")

    if "**_build_launch_kwargs(headless=True)" not in text:
        text = re.sub(
            r"await playwright\.chromium\.launch\(headless=True, channel=\"chrome\"\)",
            "await playwright.chromium.launch(**_build_launch_kwargs(headless=True))",
            text,
        )
        applied.append("launch_kwargs")
    if "**_build_launch_kwargs(headless=headless)" not in text:
        text = re.sub(
            r"await playwright\.chromium\.launch\(headless=headless, channel=\"chrome\"\)",
            "await playwright.chromium.launch(**_build_launch_kwargs(headless=headless))",
            text,
        )
    if "**_build_launch_kwargs(headless=self.headless)" not in text:
        text = re.sub(
            r"await playwright\.chromium\.launch\(headless=self\.headless, channel=\"chrome\"\)",
            "await playwright.chromium.launch(**_build_launch_kwargs(headless=self.headless))",
            text,
        )

    if "_douyin_goto(page," not in text:
        text = text.replace(
            'await page.goto("https://creator.douyin.com/")',
            'await _douyin_goto(page, "https://creator.douyin.com/")',
        )
        text = text.replace(
            'await page.goto("https://creator.douyin.com/creator-micro/content/upload")',
            'await _douyin_goto(page, "https://creator.douyin.com/creator-micro/content/upload")',
        )
        applied.append("goto")

    if "launch_persistent_context" not in text:
        cookie_gen_pattern = (
            r"        browser = await playwright\.chromium\.launch\(\*\*_build_launch_kwargs\(headless=headless\)\)\n"
            r"        context = await browser\.new_context\(\)\n"
            r"        context = await set_init_script\(context\)\n"
            r"        qrcode_path = None\n"
            r"        result = _build_login_result\(False, \"failed\", \"抖音登录失败\", account_file\)\n"
            r"        try:\n"
            r"            page = await context\.new_page\(\)"
        )
        if re.search(cookie_gen_pattern, text):
            text = re.sub(cookie_gen_pattern, _patch_block(COOKIE_GEN_BLOCK), text, count=1)
            applied.append("persistent_context")

    if "storage_state=account_file, **_build_context_kwargs()" not in text:
        text = re.sub(
            r"await browser\.new_context\(storage_state=account_file\)",
            "await browser.new_context(storage_state=account_file, **_build_context_kwargs())",
            text,
        )
        applied.append("context_kwargs")

    before_busy = text
    text = _fix_douyin_busy_check(text)
    if text != before_busy:
        applied.append("busy_check")

    if "if browser:\n                await browser.close()" not in text:
        text = text.replace(
            "            await context.close()\n            await browser.close()",
            "            await context.close()\n            if browser:\n                await browser.close()",
            1,
        )
        applied.append("browser_close")

    cookie_auth_pattern = r"async def cookie_auth\(account_file\):.*?async def douyin_setup\("
    if "_cookie_auth_once" not in text and re.search(cookie_auth_pattern, text, re.DOTALL):
        text = re.sub(
            cookie_auth_pattern,
            _patch_block(COOKIE_AUTH) + "\n\n\nasync def douyin_setup(",
            text,
            count=1,
            flags=re.DOTALL,
        )
        applied.append("cookie_auth")

    if 'await page.wait_for_url("https://creator.douyin.com/creator-micro/content/upload")' in text:
        text = re.sub(
            r'(\s*)await page\.wait_for_url\("https://creator\.douyin\.com/creator-micro/content/upload"\)',
            r'\1if "creator-micro/content/upload" not in page.url:\n\1    raise RuntimeError(f"未能进入抖音上传页: {page.url}")',
            text,
        )
        applied.append("upload_url_check")

    if "**_build_context_kwargs(),\n        )" not in text:
        text = re.sub(
            r"storage_state=f\"\{self\.account_file\}\",\n            permissions=\[\"geolocation\"\],\n        \)",
            'storage_state=f"{self.account_file}",\n            permissions=["geolocation"],\n            **_build_context_kwargs(),\n        )',
            text,
        )
        applied.append("uploader_context")

    if DOUYIN_SKIP_COVER_MARKER not in text and DOUYIN_SKIP_COVER_OLD in text:
        text = text.replace(DOUYIN_SKIP_COVER_OLD, DOUYIN_SKIP_COVER_NEW, 1)
        applied.append("skip_cover")

    import ast

    try:
        ast.parse(text)
    except SyntaxError as exc:
        print(f"抖音补丁导致语法错误: {exc}", file=sys.stderr)
        print(
            "请从 SAU 恢复 uploader/douyin_uploader/main.py 后重跑 apply_sau_patches.py",
            file=sys.stderr,
        )
        sys.exit(1)

    path.write_text(text, encoding="utf-8")
    if applied:
        print(f"已打抖音补丁({', '.join(applied)}): {path}")
    else:
        print(f"抖音补丁已是最新，跳过: {path}")


# 小红书创作页 goto 容错：默认 wait_until="load" 在创作页常 30s 不触发被误判 cookie 失效，
# 改成等 domcontentloaded + 放宽超时到 60s。
XHS_GOTO_OLD = "await page.goto(XHS_PUBLISH_VIDEO_URL)"
XHS_GOTO_NEW = 'await page.goto(XHS_PUBLISH_VIDEO_URL, wait_until="domcontentloaded", timeout=60000)'

# cookie 校验：超时重试 + 放宽 goto，避免网络慢被误判「cookie 失效」
XHS_COOKIE_AUTH_MARKER = "AIVIDEO_PATCH: xhs cookie_auth retry"
_XHS_COOKIE_AUTH_PATTERN = re.compile(
    r"async def cookie_auth\(account_file\):.*?^async def xiaohongshu_setup\(",
    re.DOTALL | re.MULTILINE,
)
XHS_COOKIE_AUTH_BLOCK = '''async def cookie_auth(account_file):
    # AIVIDEO_PATCH: xhs cookie_auth retry
    for attempt in range(3):
        if await _xhs_cookie_auth_once(account_file):
            return True
        if attempt < 2:
            await asyncio.sleep(3)
    return False


async def _xhs_cookie_auth_once(account_file):
    timeout_ms = int(os.environ.get("AIVIDEO_XHS_GOTO_TIMEOUT_MS", "90000"))
    if not os.path.exists(account_file):
        return False

    async with async_playwright() as playwright:
        if LOCAL_CHROME_PATH:
            browser = await playwright.chromium.launch(headless=True, executable_path=LOCAL_CHROME_PATH)
        else:
            browser = await playwright.chromium.launch(headless=True, channel="chrome")
        try:
            context = await browser.new_context(storage_state=account_file)
            context = await set_init_script(context)
            page = await context.new_page()
            await page.goto(
                XHS_PUBLISH_VIDEO_URL,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            await page.wait_for_timeout(3000)

            if page.url.startswith(XHS_LOGIN_URL):
                xiaohongshu_logger.info(_msg("🥹", "cookie 已失效，得重新登录一下"))
                return False

            login_box = page.locator(XHS_LOGIN_BOX_SELECTOR).first
            if await login_box.count():
                try:
                    if await login_box.is_visible():
                        xiaohongshu_logger.info(_msg("🥹", "页面仍然停留在登录二维码页，按 cookie 失效处理"))
                        return False
                except Exception:
                    return False

            xiaohongshu_logger.success(_msg("🥳", "cookie 有效"))
            return True
        except Exception as exc:
            xiaohongshu_logger.warning(_msg("😵", f"cookie 校验时出错（将重试）: {exc}"))
            return False
        finally:
            await browser.close()


async def xiaohongshu_setup('''

XHS_VALIDATE_BASE_OLD = """    async def validate_base_args(self):
        if not os.path.exists(self.account_file):
            raise RuntimeError(f"cookie文件不存在，请先完成小红书登录: {self.account_file}")
        if not await cookie_auth(self.account_file):
            raise RuntimeError(f"cookie文件已失效，请先完成小红书登录: {self.account_file}")"""

XHS_VALIDATE_BASE_NEW = """    async def validate_base_args(self):
        if not os.path.exists(self.account_file):
            raise RuntimeError(f"cookie文件不存在，请先完成小红书登录: {self.account_file}")
        # AIVIDEO_PATCH: 发布入口已校验过 cookie 时跳过二次打开创作页（避免连开浏览器超时误判失效）
        if os.environ.get("AIVIDEO_SKIP_XHS_COOKIE_RECHECK") != "1":
            if not await cookie_auth(self.account_file):
                raise RuntimeError(f"cookie文件已失效，请先完成小红书登录: {self.account_file}")"""

# 小红书设置封面容错标记 + try/except 包裹：封面弹窗 DOM 经常改版，
# 设封面失败时跳过（改用视频首帧）而不是抛异常中断整条发布。
XHS_THUMB_MARKER = "跳过自定义封面（改用视频首帧）"
_XHS_THUMB_PATTERN = re.compile(
    r'        xiaohongshu_logger\.info\(_msg\("🖼️", "小人准备设置封面"\)\)\n\n'
    r'        cover_plugin_title = .*?'
    r'        xiaohongshu_logger\.success\(_msg\("🥳", "封面已经设置完成"\)\)',
    re.DOTALL,
)
XHS_THUMB_NEW = '''        xiaohongshu_logger.info(_msg("🖼️", "小人准备设置封面"))

        # 封面是可选项：小红书封面弹窗 DOM 经常改版，任一步骤超时/找不到控件时
        # 不再抛异常中断整条发布，而是跳过自定义封面（改用视频首帧），保证视频能发出去。
        try:
            cover_plugin_title = page.locator("div.cover-plugin-title").filter(has_text="设置封面")
            cover_upload_dialog = cover_plugin_title.locator(
                "xpath=ancestor::div[contains(@class, 'cover-plugin-preview')]"
            ).locator("div.cover > div.default:visible")
            await cover_upload_dialog.wait_for(state="visible", timeout=30000)

            await cover_upload_dialog.click(force=True)

            modal = page.locator("div.d-modal.cover-modal")
            await modal.wait_for(state="visible", timeout=30000)

            file_input = modal.locator('input[type="file"][accept*="image"]').first
            await file_input.wait_for(state="attached", timeout=10000)
            await file_input.set_input_files(thumbnail_path)
            await page.wait_for_timeout(2000)

            confirm_button = modal.locator("button.mojito-button").filter(has_text="确定").first
            await confirm_button.wait_for(state="visible", timeout=10000)
            await confirm_button.click()

            await modal.wait_for(state="hidden", timeout=30000)
            xiaohongshu_logger.success(_msg("🥳", "封面已经设置完成"))
        except Exception as exc:
            xiaohongshu_logger.warning(
                _msg("😵", f"设置封面失败，跳过自定义封面（改用视频首帧），继续发布: {exc}")
            )
            # 关掉可能半开的封面弹窗，避免挡住后面的发布按钮
            try:
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(500)
            except Exception:
                pass'''


# 小红书发布确认循环加固：原版是 while True 无超时死循环，发布按钮点不中时会空转到崩溃。
XHS_PUBLISH_MARKER = "AIVIDEO_PATCH: 发布确认最多尝试 90 秒"
_XHS_PUBLISH_PATTERN = re.compile(
    r'        while True:\n'
    r'            try:\n'
    r'                if self\.publish_strategy == XIAOHONGSHU_PUBLISH_STRATEGY_SCHEDULED:\n'
    r'                    await page\.locator\(\'button:has-text\("定时发布"\)\'\)\.click\(\)\n'
    r'                else:\n'
    r'                    await page\.locator\(\'button:has-text\("发布"\)\'\)\.click\(\)\n'
    r'                await page\.wait_for_url\(\n'
    r'                    "https://creator\.xiaohongshu\.com/publish/success\?\*\*",\n'
    r'                    timeout=3000\n'
    r'                \)\n'
    r'                xiaohongshu_logger\.success\(_msg\("🥳", "视频发布成功，小人开心收工"\)\)\n'
    r'                break\n'
    r'            except Exception:\n'
    r'                xiaohongshu_logger\.info\(_msg\("🏃", "小人正在冲刺发布视频"\)\)\n'
    r'                if self\.debug:\n'
    r'                    await page\.screenshot\(full_page=True\)\n'
    r'                await asyncio\.sleep\(0\.5\)',
    re.DOTALL,
)
XHS_PUBLISH_NEW = '''        # AIVIDEO_PATCH: 发布确认最多尝试 90 秒，避免 while True 死循环卡死/崩溃；
        # 成功判定除了跳转 success 页，再兜底看页面是否出现“发布成功”提示。
        publish_btn_text = (
            "定时发布"
            if self.publish_strategy == XIAOHONGSHU_PUBLISH_STRATEGY_SCHEDULED
            else "发布"
        )
        deadline = asyncio.get_event_loop().time() + 90
        published = False
        while asyncio.get_event_loop().time() < deadline:
            try:
                await page.locator(f'button:has-text("{publish_btn_text}")').last.click(timeout=6000)
            except Exception:
                pass
            try:
                await page.wait_for_url(
                    "https://creator.xiaohongshu.com/publish/success?**",
                    timeout=3000,
                )
                published = True
                break
            except Exception:
                pass
            try:
                for ok_text in ("发布成功", "发布完成"):
                    if await page.get_by_text(ok_text).first.is_visible():
                        published = True
                        break
                if published:
                    break
            except Exception:
                pass
            xiaohongshu_logger.info(_msg("🏃", "小人正在冲刺发布视频"))
            await asyncio.sleep(1)
        if published:
            xiaohongshu_logger.success(_msg("🥳", "视频发布成功，小人开心收工"))
        else:
            raise RuntimeError("小红书发布按钮已点击但未确认成功（可能页面改版/网络问题），请人工核对小红书后台")'''

XHS_SKIP_COVER_MARKER = "AIVIDEO_PATCH: 跳过封面设置"
XHS_SKIP_COVER_OLD = "        await self.set_thumbnail(page, self.thumbnail_path)"
XHS_SKIP_COVER_NEW = """        # AIVIDEO_PATCH: 跳过封面设置，使用视频默认首帧
        xiaohongshu_logger.info(_msg("🖼️", "跳过自定义封面，使用视频默认首帧"))"""

DOUYIN_SKIP_COVER_MARKER = "AIVIDEO_PATCH: 跳过自定义封面"
DOUYIN_SKIP_COVER_OLD = "        await self.set_thumbnail(page)"
DOUYIN_SKIP_COVER_NEW = "        # AIVIDEO_PATCH: 跳过自定义封面，使用视频默认首帧"


def patch_xhs(path: Path) -> None:
    if not path.is_file():
        print(f"跳过小红书补丁：未找到 {path}", file=sys.stderr)
        return
    text = path.read_text(encoding="utf-8")
    applied: list[str] = []

    # 1) fill_tags 容错（整段替换至 fill_meta，修复旧版部分补丁留下的孤立 except）
    fill_tags_match = _XHS_FILL_TAGS_FUNC.search(text)
    if fill_tags_match and fill_tags_match.group(0).strip() != XHS_FILL_TAGS.strip():
        text = _XHS_FILL_TAGS_FUNC.sub(XHS_FILL_TAGS + "\n\n", text, count=1)
        applied.append("fill_tags")
    elif not fill_tags_match:
        print("小红书 fill_tags 补丁锚点缺失（upstream 可能已变更），跳过", file=sys.stderr)

    # 2) 创作页 goto 容错（domcontentloaded + 60s 超时），覆盖 cookie 校验与上传两处
    if XHS_GOTO_OLD in text:
        text = text.replace(XHS_GOTO_OLD, XHS_GOTO_NEW)
        applied.append("goto")

    # 3) 设置封面失败时跳过而不崩溃
    if XHS_THUMB_MARKER not in text and _XHS_THUMB_PATTERN.search(text):
        text = _XHS_THUMB_PATTERN.sub(lambda _m: XHS_THUMB_NEW, text, count=1)
        applied.append("set_thumbnail")

    # 4) 发布确认循环加固：限时 90s + 兜底成功判定，避免 while True 死循环卡死/崩溃
    if XHS_PUBLISH_MARKER not in text and _XHS_PUBLISH_PATTERN.search(text):
        text = _XHS_PUBLISH_PATTERN.sub(lambda _m: XHS_PUBLISH_NEW, text, count=1)
        applied.append("publish_loop")

    # 5) cookie 校验重试 + 90s 超时（默认）
    if XHS_COOKIE_AUTH_MARKER not in text and _XHS_COOKIE_AUTH_PATTERN.search(text):
        text = _XHS_COOKIE_AUTH_PATTERN.sub(XHS_COOKIE_AUTH_BLOCK, text, count=1)
        applied.append("cookie_auth_retry")

    # 6) 发布前已校验 cookie 时跳过二次校验
    if "AIVIDEO_PATCH: 发布入口已校验过 cookie" not in text and XHS_VALIDATE_BASE_OLD in text:
        text = text.replace(XHS_VALIDATE_BASE_OLD, XHS_VALIDATE_BASE_NEW)
        applied.append("skip_recheck")

    # 7) 跳过封面设置（使用视频默认首帧）
    if XHS_SKIP_COVER_MARKER not in text and XHS_SKIP_COVER_OLD in text:
        text = text.replace(XHS_SKIP_COVER_OLD, XHS_SKIP_COVER_NEW, 1)
        applied.append("skip_cover")

    path.write_text(text, encoding="utf-8")
    if applied:
        print(f"已打小红书补丁({', '.join(applied)}): {path}")
    else:
        print(f"小红书补丁已是最新，跳过: {path}")


def patch_tencent(path: Path) -> None:
    if not path.is_file():
        print(f"跳过视频号补丁：未找到 {path}", file=sys.stderr)
        return
    text = path.read_text(encoding="utf-8")
    if TENCENT_UPLOAD_MARKER in text:
        print(f"视频号补丁已是最新，跳过: {path}")
        return
    if TENCENT_UPLOAD_OLD not in text:
        print("视频号 upload 补丁锚点缺失（upstream 可能已变更），跳过", file=sys.stderr)
        return
    text = text.replace(TENCENT_UPLOAD_OLD, TENCENT_UPLOAD_NEW, 1)
    path.write_text(text, encoding="utf-8")
    print(f"已打视频号补丁(skip_cover): {path}")


def patch_biliup_runtime(path: Path) -> None:
    if not path.is_file():
        print(f"跳过 biliup 补丁：未找到 {path}", file=sys.stderr)
        return
    text = path.read_text(encoding="utf-8")
    if BILIUP_GH_PROXY_MARKER in text:
        return
    if "import requests" not in text:
        print("biliup runtime 结构异常，跳过 GH_PROXY 补丁", file=sys.stderr)
        return
    helper = '''
def _github_proxy_url(url: str) -> str:
    """AIVIDEO_PATCH: GH_PROXY for biliup download"""
    import os

    proxy = os.environ.get("GH_PROXY", "").strip().rstrip("/")
    if not proxy or "github.com" not in url:
        return url
    return f"{proxy}/{url}"


'''
    text = text.replace("import requests\n", "import requests\n" + helper, 1)
    text = text.replace(
        "response = requests.get(\n        GITHUB_RELEASE_API,",
        "response = requests.get(\n        _github_proxy_url(GITHUB_RELEASE_API),",
        1,
    )
    text = text.replace(
        'return {\n                "asset_name": asset_name,\n                "asset_url": asset.get("browser_download_url", ""),\n            }',
        'return {\n                "asset_name": asset_name,\n                "asset_url": _github_proxy_url(asset.get("browser_download_url", "")),\n            }',
        1,
    )
    text = text.replace(
        "with requests.get(release[\"asset_url\"], stream=True, timeout=120) as response:",
        'with requests.get(_github_proxy_url(release["asset_url"]), stream=True, timeout=120) as response:',
        1,
    )
    path.write_text(text, encoding="utf-8")
    print(f"已打 biliup 补丁(GH_PROXY): {path}")


if __name__ == "__main__":
    patch(TARGET)
    patch_xhs(XHS_TARGET)
    patch_tencent(TENCENT_TARGET)
    patch_biliup_runtime(BILIUP_RUNTIME)
