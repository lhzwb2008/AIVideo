#!/usr/bin/env python3
"""测试 Instagram / Facebook Reels / LinkedIn 浏览器自动发布（不入主流程）。

用法:
  ./scripts/test-us-social.sh login instagram
  ./scripts/test-us-social.sh check all
  ./scripts/test-us-social.sh publish instagram --video output/en/xxx.mp4 --assist
  ./scripts/test-us-social.sh publish all --script logs/en/last_script_*.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paths import ROOT as PROJECT_ROOT  # noqa: E402

PLATFORMS = ("instagram", "facebook", "linkedin")

PLATFORM_LABEL = {
    "instagram": "Instagram Reels",
    "facebook": "Facebook Reels",
    "linkedin": "LinkedIn",
}


class SocialTestError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def sau_home() -> Path:
    custom = _env("SAU_HOME")
    if custom:
        return Path(custom).expanduser()
    return PROJECT_ROOT / "vendor" / "social-auto-upload"


def cookie_path(platform: str, account: str | None = None) -> Path:
    account = account or _env("US_SOCIAL_ACCOUNT", "main")
    path = sau_home() / "cookies" / f"{platform}_{account}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def profile_dir(platform: str, account: str | None = None) -> Path:
    account = account or _env("US_SOCIAL_ACCOUNT", "main")
    path = sau_home() / "cookies" / "browser_profiles" / f"{platform}_{account}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _chrome_path() -> str:
    for path in (
        _env("LOCAL_CHROME_PATH"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ):
        if path and Path(path).is_file():
            return path
    return ""


def _ensure_patchright() -> None:
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
        raise SocialTestError("未安装 patchright，请先 ./setup-sau.sh") from exc


def _http_proxy() -> str:
    for key in ("US_SOCIAL_HTTP_PROXY", "YOUTUBE_HTTP_PROXY", "HTTPS_PROXY", "https_proxy"):
        val = _env(key)
        if val:
            return val
    return ""


def _launch_kwargs(*, headed: bool) -> dict:
    launch = {
        "headless": not headed,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--lang=en-US",
            "--no-first-run",
            "--window-size=1440,900",
        ],
    }
    proxy = _http_proxy()
    if proxy:
        launch["proxy"] = {"server": proxy}
    chrome = _chrome_path()
    if chrome:
        launch["executable_path"] = chrome
    else:
        launch["channel"] = "chrome"
    return launch


async def _new_context(browser, platform: str, *, headed: bool):
    cookie = cookie_path(platform)
    kwargs = {
        "locale": "en-US",
        "timezone_id": "America/New_York",
        "viewport": {"width": 1440, "height": 900},
    }
    if cookie.is_file():
        kwargs["storage_state"] = str(cookie)
    context = await browser.new_context(**kwargs)
    try:
        home = str(sau_home())
        if home not in sys.path:
            sys.path.insert(0, home)
        from utils.base_social_media import set_init_script

        context = await set_init_script(context)
    except Exception:
        pass
    return context


async def _save_cookie(context, platform: str) -> None:
    await context.storage_state(path=str(cookie_path(platform)))


async def _dismiss_common(page) -> None:
    for text in (
        "Not Now",
        "Not now",
        "Turn on",
        "Allow all cookies",
        "Accept All",
        "Accept",
        "Dismiss",
        "Close",
        "Maybe later",
        "Remind me later",
    ):
        try:
            btn = page.get_by_role("button", name=text, exact=False).first
            if await btn.count() and await btn.is_visible():
                await btn.click(timeout=2000)
                await asyncio.sleep(0.5)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

async def _loc_count(page, selector: str) -> int:
    try:
        return await page.locator(selector).count()
    except Exception:
        return 0


async def _wait_logged_in(page, platform: str, *, timeout_s: int = 300) -> bool:
    for i in range(timeout_s):
        url = page.url.lower()
        try:
            if platform == "instagram":
                if "instagram.com" not in url:
                    pass
                elif any(x in url for x in ("/accounts/login", "/accounts/emailsignup")):
                    pass
                elif await _loc_count(page, 'input[name="password"]') > 0:
                    pass
                elif (
                    await _loc_count(page, 'svg[aria-label="Home"]') > 0
                    or await _loc_count(page, 'a[href="/"] svg') > 0
                    or await page.get_by_role("link", name="Profile").count() > 0
                    or await _loc_count(page, 'img[alt*="profile picture"]') > 0
                    or await page.get_by_text("Home", exact=True).count() > 0
                ):
                    return True
            elif platform == "facebook":
                if "facebook.com" in url and "login" not in url:
                    if (
                        await _loc_count(page, '[aria-label="Your profile"]') > 0
                        or await _loc_count(page, '[aria-label="Account"]') > 0
                        or await page.get_by_role("link", name="Home").count() > 0
                        or await _loc_count(page, 'input[name="pass"]') == 0
                    ):
                        return True
            elif platform == "linkedin":
                if "linkedin.com" in url and "login" not in url and "checkpoint" not in url:
                    if (
                        await _loc_count(page, ".share-box") > 0
                        or await _loc_count(page, "button.share-box-feed-entry__trigger") > 0
                        or await page.get_by_role("button", name="Start a post").count() > 0
                        or "feed" in url
                    ):
                        return True
        except Exception:
            pass
        if i and i % 15 == 0:
            hint = ""
            if any(x in url for x in ("codeentry", "challenge", "two_factor", "checkpoint")):
                hint = " ← 请在浏览器完成验证码/二次验证"
            print(f"  等待登录… ({i}s) 当前: {page.url[:80]}{hint}", flush=True)
        await asyncio.sleep(1)
    return False


async def _wait_enter(prompt: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: input(prompt))


async def login_platform(platform: str, *, force: bool = False, manual: bool = False) -> None:
    _ensure_patchright()
    from patchright.async_api import async_playwright

    cookie = cookie_path(platform)
    prof = profile_dir(platform)
    if force:
        if cookie.is_file():
            cookie.unlink()
        import shutil

        if prof.is_dir():
            shutil.rmtree(prof, ignore_errors=True)
            prof.mkdir(parents=True, exist_ok=True)

    urls = {
        "instagram": "https://www.instagram.com/accounts/login/",
        "facebook": "https://www.facebook.com/login/",
        "linkedin": "https://www.linkedin.com/login",
    }
    proxy = _http_proxy()
    print(f"\n[{PLATFORM_LABEL[platform]}] 打开浏览器，请手动完成登录…", flush=True)
    if proxy:
        print(f"  代理: {proxy}", flush=True)
    if manual:
        print("  手动模式：在浏览器完成登录后，回到终端按 Enter 保存 cookie", flush=True)
    else:
        print("  登录成功后脚本会自动检测并保存 cookie（最多等 10 分钟）", flush=True)
        print("  若自动检测失败，可在终端按 Enter 强制保存", flush=True)
    print("  若出现验证码/二次验证，请在浏览器内完成", flush=True)

    launch = _launch_kwargs(headed=True)
    launch.pop("headless", None)
    user_data = str(prof)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data,
            headless=False,
            locale="en-US",
            timezone_id="America/New_York",
            viewport={"width": 1440, "height": 900},
            proxy=launch.get("proxy"),
            executable_path=launch.get("executable_path"),
            channel=None if launch.get("executable_path") else launch.get("channel", "chrome"),
            args=launch.get("args", []),
        )
        try:
            home = str(sau_home())
            if home not in sys.path:
                sys.path.insert(0, home)
            from utils.base_social_media import set_init_script

            context = await set_init_script(context)
        except Exception:
            pass
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            await page.goto(urls[platform], wait_until="domcontentloaded", timeout=90_000)
            if manual:
                await _wait_enter("\n>>> 已在浏览器登录？按 Enter 保存 cookie: ")
            else:
                ok = await _wait_logged_in(page, platform, timeout_s=600)
                if not ok:
                    print(
                        f"\n  自动检测未通过（当前: {page.url[:100]}）",
                        flush=True,
                    )
                    await _wait_enter(">>> 若浏览器里已看到首页，按 Enter 强制保存 cookie: ")
            await _save_cookie(context, platform)
            print(f"  ✓ cookie 已保存: {cookie}", flush=True)
        finally:
            await context.close()


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------

async def check_platform(platform: str) -> bool:
    cookie = cookie_path(platform)
    if not cookie.is_file():
        print(f"  ✗ {PLATFORM_LABEL[platform]}: 无 cookie ({cookie})", flush=True)
        return False

    _ensure_patchright()
    from patchright.async_api import async_playwright

    urls = {
        "instagram": "https://www.instagram.com/",
        "facebook": "https://www.facebook.com/",
        "linkedin": "https://www.linkedin.com/feed/",
    }
    async with async_playwright() as p:
        browser = await p.chromium.launch(**_launch_kwargs(headed=False))
        context = await _new_context(browser, platform, headed=False)
        page = await context.new_page()
        try:
            await page.goto(urls[platform], wait_until="domcontentloaded", timeout=90_000)
            await asyncio.sleep(3)
            ok = await _wait_logged_in(page, platform, timeout_s=8)
            if ok:
                print(f"  ✓ {PLATFORM_LABEL[platform]}: 登录态有效", flush=True)
                await _save_cookie(context, platform)
            else:
                print(
                    f"  ✗ {PLATFORM_LABEL[platform]}: 登录态失效，请 "
                    f"./scripts/test-us-social.sh login {platform}",
                    flush=True,
                )
            return ok
        finally:
            await context.close()
            await browser.close()


# ---------------------------------------------------------------------------
# Caption helpers
# ---------------------------------------------------------------------------

def _build_caption(script_path: Path | None) -> tuple[str, str]:
    """返回 (title, caption)。"""
    title = "Market Sketch — US markets in plain English"
    caption = (
        "US markets in plain English — one sketch at a time.\n"
        "#stocks #USmarket #finance #investing #wallstreet\n"
        "For education only. Not investment advice."
    )
    if not script_path or not script_path.is_file():
        return title, caption
    try:
        os.environ.setdefault("AIVIDEO_LOCALE", "en")
        from tiktok_caption import build_tiktok_fields

        data = json.loads(script_path.read_text(encoding="utf-8"))
        script = data.get("script", data)
        fields = build_tiktok_fields(script)
        title = (fields.get("title") or script.get("title") or title).split("\n")[0][:220]
        tags = fields.get("tags") or []
        tag_line = " ".join(f"#{t}" for t in tags) if isinstance(tags, list) else ""
        caption = fields.get("title") or title
        if tag_line and tag_line not in caption:
            caption = f"{caption}\n{tag_line}"
        return title, caption[:2200]
    except Exception:
        return title, caption


def _resolve_video(path: str | None) -> Path:
    if path:
        video = Path(path)
        if not video.is_absolute():
            video = PROJECT_ROOT / video
        if video.is_file():
            return video
        raise SocialTestError(f"视频不存在: {video}")
    last = PROJECT_ROOT / "logs" / "en" / "last_video.txt"
    if last.is_file():
        raw = last.read_text(encoding="utf-8").strip()
        video = Path(raw)
        if not video.is_absolute():
            video = PROJECT_ROOT / video
        if video.is_file():
            return video
    from locale_env import latest_output_video

    found = latest_output_video("en")
    if found:
        return found
    raise SocialTestError("未找到测试视频，请传 --video")


# ---------------------------------------------------------------------------
# Publish flows
# ---------------------------------------------------------------------------

async def _set_files_on_input(page, video: Path) -> None:
    selectors = (
        "input[type='file'][accept*='video']",
        "input[type='file']",
    )
    for _ in range(60):
        for sel in selectors:
            loc = page.locator(sel).first
            if await loc.count():
                try:
                    await loc.set_input_files(str(video), timeout=30_000)
                    return
                except Exception:
                    pass
        await asyncio.sleep(1)
    raise SocialTestError("未找到视频 file input")


async def _click_if_visible(page, *, role: str | None = None, name: str | None = None, selector: str | None = None) -> bool:
    try:
        if selector:
            loc = page.locator(selector).first
        else:
            loc = page.get_by_role(role or "button", name=name or "", exact=False).first
        if await loc.count() and await loc.is_visible():
            await loc.click(timeout=5000)
            return True
    except Exception:
        pass
    return False


async def publish_instagram(page, video: Path, caption: str, *, assist: bool) -> None:
    await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=90_000)
    await _dismiss_common(page)
    await asyncio.sleep(2)

    # 桌面端：左侧 Create → 选文件（竖屏视频会走 Reels 流程）
    opened = False
    for sel in (
        'svg[aria-label="Create"]',
        'svg[aria-label="New post"]',
        '[aria-label="Create"]',
        '[aria-label="New post"]',
    ):
        if await _click_if_visible(page, selector=sel):
            opened = True
            break
    if not opened:
        await _click_if_visible(page, role="link", name="Create")

    await asyncio.sleep(1)
    for name in ("Select from computer", "Select from Computer"):
        if await _click_if_visible(page, role="button", name=name):
            break
    for name in ("Reel", "Reels"):
        await _click_if_visible(page, role="menuitem", name=name)
        await _click_if_visible(page, role="button", name=name)

    await asyncio.sleep(1)
    await _set_files_on_input(page, video)
    print("  已选择视频", flush=True)

    for _ in range(90):
        if await _click_if_visible(page, role="button", name="OK"):
            break
        if await page.locator("button:has-text('OK')").count():
            break
        await asyncio.sleep(1)

    for _ in range(3):
        await asyncio.sleep(2)
        if not await _click_if_visible(page, role="button", name="Next"):
            await _click_if_visible(page, selector="div[role='button']:has-text('Next')")
        else:
            break

    await asyncio.sleep(1)
    # Caption 编辑区
    for sel in (
        "div[aria-label='Write a caption...']",
        "textarea[aria-label='Write a caption...']",
        "[contenteditable='true']",
        "div[role='textbox']",
    ):
        loc = page.locator(sel).first
        if await loc.count():
            try:
                await loc.click(timeout=3000)
                await loc.fill(caption[:2200])
                break
            except Exception:
                try:
                    await page.keyboard.type(caption[:500])
                except Exception:
                    pass
            break

    if assist:
        shot = PROJECT_ROOT / "logs" / "test_instagram_assist.png"
        await page.screenshot(path=str(shot), full_page=True)
        print(f"  [assist] 已填好，请手动点 Share。截图: {shot}", flush=True)
        await asyncio.sleep(120)
        return

    for _ in range(30):
        if await _click_if_visible(page, role="button", name="Share"):
            print("  已点击 Share", flush=True)
            break
        await asyncio.sleep(2)
    await asyncio.sleep(8)
    print("  Instagram 发布流程已提交", flush=True)


async def publish_facebook(page, video: Path, caption: str, *, assist: bool) -> None:
    for url in (
        "https://business.facebook.com/latest/reels_composer",
        "https://www.facebook.com/reels/create",
    ):
        await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        await _dismiss_common(page)
        await asyncio.sleep(2)
        if "login" not in page.url.lower():
            break

    if "login" in page.url.lower():
        raise SocialTestError("Facebook 未登录")

    for name in ("Add video", "Add Video", "Upload video", "Photo/video"):
        if await _click_if_visible(page, role="button", name=name):
            break
    await _set_files_on_input(page, video)
    print("  已选择视频", flush=True)

    for sel in (
        "div[aria-label='Describe your reel']",
        "div[role='textbox']",
        "[contenteditable='true']",
    ):
        loc = page.locator(sel).first
        if await loc.count():
            try:
                await loc.click(timeout=3000)
                await loc.fill(caption[:2200])
                break
            except Exception:
                pass

    if assist:
        shot = PROJECT_ROOT / "logs" / "test_facebook_assist.png"
        await page.screenshot(path=str(shot), full_page=True)
        print(f"  [assist] 请手动点 Post/Reels 发布。截图: {shot}", flush=True)
        await asyncio.sleep(120)
        return

    for name in ("Post", "Share", "Publish", "Next"):
        if await _click_if_visible(page, role="button", name=name):
            print(f"  已点击 {name}", flush=True)
            break
    await asyncio.sleep(8)
    print("  Facebook Reels 发布流程已提交", flush=True)


async def publish_linkedin(page, video: Path, caption: str, *, assist: bool) -> None:
    await page.goto(
        "https://www.linkedin.com/feed/",
        wait_until="domcontentloaded",
        timeout=90_000,
    )
    await _dismiss_common(page)
    await asyncio.sleep(2)

    # 打开发帖框
    for sel in (
        "button.share-box-feed-entry__trigger",
        "button:has-text('Start a post')",
        ".share-box-feed-entry__trigger",
    ):
        if await _click_if_visible(page, selector=sel):
            break
    await asyncio.sleep(1)

    # 添加视频
    for name in ("Video", "Add a video"):
        if await _click_if_visible(page, role="button", name=name):
            break
    await asyncio.sleep(1)
    await _set_files_on_input(page, video)
    print("  已选择视频，等待上传…", flush=True)

    for i in range(120):
        if await page.locator("button:has-text('Post'), button.share-actions__primary-action").count():
            break
        if i and i % 10 == 0:
            print(f"  上传中… ({i * 2}s)", flush=True)
        await asyncio.sleep(2)

    for sel in (
        "div.ql-editor",
        "div[role='textbox']",
        ".share-creation-state__text-editor div[contenteditable='true']",
    ):
        loc = page.locator(sel).first
        if await loc.count():
            try:
                await loc.click(timeout=3000)
                await loc.fill(caption[:3000])
                break
            except Exception:
                pass

    if assist:
        shot = PROJECT_ROOT / "logs" / "test_linkedin_assist.png"
        await page.screenshot(path=str(shot), full_page=True)
        print(f"  [assist] 请手动点 Post。截图: {shot}", flush=True)
        await asyncio.sleep(120)
        return

    for sel in (
        "button.share-actions__primary-action",
        "button:has-text('Post')",
    ):
        if await _click_if_visible(page, selector=sel):
            print("  已点击 Post", flush=True)
            break
    await asyncio.sleep(8)
    print("  LinkedIn 发布流程已提交", flush=True)


async def publish_platform(
    platform: str,
    video: Path,
    *,
    caption: str,
    assist: bool = False,
    headed: bool = True,
) -> None:
    cookie = cookie_path(platform)
    if not cookie.is_file():
        raise SocialTestError(
            f"无 cookie: {cookie}\n请先: ./scripts/test-us-social.sh login {platform}"
        )

    _ensure_patchright()
    from patchright.async_api import async_playwright

    print(
        f"\n[{PLATFORM_LABEL[platform]}] 发布测试 | "
        f"{'半自动' if assist else '自动'} | {video.name}",
        flush=True,
    )
    async with async_playwright() as p:
        browser = await p.chromium.launch(**_launch_kwargs(headed=headed or assist))
        context = await _new_context(browser, platform, headed=True)
        page = await context.new_page()
        try:
            if platform == "instagram":
                await publish_instagram(page, video, caption, assist=assist)
            elif platform == "facebook":
                await publish_facebook(page, video, caption, assist=assist)
            elif platform == "linkedin":
                await publish_linkedin(page, video, caption, assist=assist)
            else:
                raise SocialTestError(f"未知平台: {platform}")
            await _save_cookie(context, platform)
        except Exception:
            shot = PROJECT_ROOT / "logs" / f"test_{platform}_fail.png"
            try:
                await page.screenshot(path=str(shot), full_page=True, timeout=60_000)
                print(f"  失败截图: {shot}", flush=True)
            except Exception:
                pass
            raise
        finally:
            if assist:
                await asyncio.sleep(2)
            await context.close()
            await browser.close()


def _expand_platforms(raw: str) -> list[str]:
    if raw == "all":
        return list(PLATFORMS)
    if raw not in PLATFORMS:
        raise SystemExit(f"未知平台: {raw}（可选: {', '.join(PLATFORMS)}, all）")
    return [raw]


def main() -> int:
    parser = argparse.ArgumentParser(description="测试 IG/FB/LinkedIn 浏览器发布")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_login = sub.add_parser("login", help="有头登录并保存 cookie")
    p_login.add_argument("platform", choices=[*PLATFORMS, "all"])
    p_login.add_argument("--force", action="store_true")
    p_login.add_argument(
        "--manual",
        action="store_true",
        help="不自动检测，登录完成后在终端按 Enter 保存 cookie",
    )

    p_check = sub.add_parser("check", help="校验 cookie")
    p_check.add_argument("platform", choices=[*PLATFORMS, "all"])

    p_pub = sub.add_parser("publish", help="试发布一条视频")
    p_pub.add_argument("platform", choices=[*PLATFORMS, "all"])
    p_pub.add_argument("--video", help="MP4 路径，默认 logs/en/last_video.txt")
    p_pub.add_argument("--script", help="脚本 JSON，用于生成 caption")
    p_pub.add_argument(
        "--assist",
        action="store_true",
        help="半自动：填好表单后停住，由你点最终发布",
    )
    p_pub.add_argument("--headless", action="store_true")

    args = parser.parse_args()

    if args.cmd == "login":
        for plat in _expand_platforms(args.platform):
            asyncio.run(login_platform(plat, force=args.force, manual=args.manual))
        return 0

    if args.cmd == "check":
        ok_all = True
        for plat in _expand_platforms(args.platform):
            if not asyncio.run(check_platform(plat)):
                ok_all = False
        return 0 if ok_all else 1

    if args.cmd == "publish":
        video = _resolve_video(args.video)
        script = Path(args.script) if args.script else None
        if script and not script.is_absolute():
            script = PROJECT_ROOT / script
        _, caption = _build_caption(script)
        errors: list[str] = []
        for plat in _expand_platforms(args.platform):
            try:
                asyncio.run(
                    publish_platform(
                        plat,
                        video,
                        caption=caption,
                        assist=args.assist,
                        headed=not args.headless,
                    )
                )
                print(f"  ✓ {PLATFORM_LABEL[plat]} 测试完成", flush=True)
            except Exception as exc:
                errors.append(f"{plat}: {exc}")
                print(f"  ✗ {PLATFORM_LABEL[plat]}: {exc}", flush=True)
        if errors:
            print("\n失败平台:", flush=True)
            for e in errors:
                print(f"  - {e}", flush=True)
            return 1
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
