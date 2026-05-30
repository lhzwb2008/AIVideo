#!/usr/bin/env python3
"""对 vendor/social-auto-upload 打 AIVideo 兼容补丁（可重复执行）。"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from paths import ROOT
TARGET = ROOT / "vendor" / "social-auto-upload" / "uploader" / "douyin_uploader" / "main.py"
XHS_TARGET = ROOT / "vendor" / "social-auto-upload" / "uploader" / "xiaohongshu_uploader" / "main.py"

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

WAIT_LOGIN_BUSY_CHECK = '''
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

    text = path.read_text(encoding="utf-8")

    helper_pattern = (
        r"def _resolve_chrome_path\(\) -> str:.*?async def _douyin_goto\(page, url: str\):\n"
        r"    return await page\.goto\(url, wait_until=\"domcontentloaded\", timeout=120_000\)"
    )
    if re.search(helper_pattern, text, re.DOTALL):
        text = re.sub(helper_pattern, HELPER.strip(), text, count=1, flags=re.DOTALL)
    elif "def _build_launch_kwargs" in text:
        simple_pattern = (
            r"def _build_launch_kwargs\(headless: bool\) -> dict:.*?async def _douyin_goto\(page, url: str\):\n"
            r"    return await page\.goto\(url, wait_until=\"domcontentloaded\", timeout=120_000\)"
        )
        text = re.sub(simple_pattern, HELPER.strip(), text, count=1, flags=re.DOTALL)
    else:
        anchor = "async def cookie_auth(account_file):"
        if anchor not in text:
            print("补丁锚点缺失，请检查 upstream 是否变更", file=sys.stderr)
            sys.exit(1)
        text = text.replace(anchor, HELPER.strip() + "\n\n\n" + anchor)

    text = re.sub(
        r"await playwright\.chromium\.launch\(headless=True, channel=\"chrome\"\)",
        "await playwright.chromium.launch(**_build_launch_kwargs(headless=True))",
        text,
    )
    text = re.sub(
        r"await playwright\.chromium\.launch\(headless=headless, channel=\"chrome\"\)",
        "await playwright.chromium.launch(**_build_launch_kwargs(headless=headless))",
        text,
    )
    text = re.sub(
        r"await playwright\.chromium\.launch\(headless=self\.headless, channel=\"chrome\"\)",
        "await playwright.chromium.launch(**_build_launch_kwargs(headless=self.headless))",
        text,
    )

    text = text.replace(
        'await page.goto("https://creator.douyin.com/")',
        'await _douyin_goto(page, "https://creator.douyin.com/")',
    )
    text = text.replace(
        'await page.goto("https://creator.douyin.com/creator-micro/content/upload")',
        'await _douyin_goto(page, "https://creator.douyin.com/creator-micro/content/upload")',
    )

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
        text = re.sub(cookie_gen_pattern, COOKIE_GEN_BLOCK.strip(), text, count=1)

    if "storage_state=account_file, **_build_context_kwargs()" not in text:
        text = re.sub(
            r"await browser\.new_context\(storage_state=account_file\)",
            "await browser.new_context(storage_state=account_file, **_build_context_kwargs())",
            text,
        )

    if "检测到系统繁忙" not in text:
        text = text.replace(
            '        expired_box = page.get_by_text("二维码失效", exact=True).locator("..").first',
            WAIT_LOGIN_BUSY_CHECK.strip() + '\n        expired_box = page.get_by_text("二维码失效", exact=True).locator("..").first',
            1,
        )

    if "if browser:\n                await browser.close()" not in text:
        text = text.replace(
            "            await context.close()\n            await browser.close()",
            "            await context.close()\n            if browser:\n                await browser.close()",
            1,
        )

    cookie_auth_pattern = r"async def cookie_auth\(account_file\):.*?async def douyin_setup\("
    if re.search(cookie_auth_pattern, text, re.DOTALL):
        text = re.sub(cookie_auth_pattern, COOKIE_AUTH.strip() + "\n\n\nasync def douyin_setup(", text, count=1, flags=re.DOTALL)

    text = text.replace(
        'await page.wait_for_url("https://creator.douyin.com/creator-micro/content/upload")',
        'if "creator-micro/content/upload" not in page.url:\n            raise RuntimeError(f"未能进入抖音上传页: {page.url}")',
    )

    text = re.sub(
        r"storage_state=f\"\{self\.account_file\}\",\n            permissions=\[\"geolocation\"\],\n        \)",
        'storage_state=f"{self.account_file}",\n            permissions=["geolocation"],\n            **_build_context_kwargs(),\n        )',
        text,
    )

    path.write_text(text, encoding="utf-8")
    print(f"已打补丁: {path}")


def patch_xhs(path: Path) -> None:
    if not path.is_file():
        print(f"跳过小红书补丁：未找到 {path}", file=sys.stderr)
        return
    text = path.read_text(encoding="utf-8")
    if "AIVIDEO_PATCH: 最多 5 个话题" in text:
        print(f"小红书补丁已存在，跳过: {path}")
        return
    pattern = (
        r"    async def fill_tags\(self, page: Page\) -> None:\n"
        r".*?await first_item\.click\(\)\n"
    )
    if not re.search(pattern, text, re.DOTALL):
        print("小红书补丁锚点缺失（upstream 可能已变更），跳过", file=sys.stderr)
        return
    text = re.sub(pattern, XHS_FILL_TAGS, text, count=1, flags=re.DOTALL)
    path.write_text(text, encoding="utf-8")
    print(f"已打小红书补丁: {path}")


if __name__ == "__main__":
    patch(TARGET)
    patch_xhs(XHS_TARGET)
