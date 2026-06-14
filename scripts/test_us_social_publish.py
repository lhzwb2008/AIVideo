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
import subprocess
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
        "关闭",
        "以后再说",
        "暂不",
        "允许",
        "接受",
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
    print(f"\n[{PLATFORM_LABEL[platform]}] 打开浏览器，请手动完成登录…", flush=True)
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

async def _set_files_via_chooser(page, video: Path, labels: tuple[str, ...]) -> bool:
    for label in labels:
        try:
            async with page.expect_file_chooser(timeout=5000) as fc_info:
                target = page.get_by_text(label, exact=False).first
                if not await target.count():
                    continue
                await target.click(timeout=5000)
            chooser = await fc_info.value
            await chooser.set_files(str(video))
            return True
        except Exception:
            pass
        try:
            async with page.expect_file_chooser(timeout=5000) as fc_info:
                await page.get_by_role("button", name=label, exact=False).first.click(timeout=5000)
            chooser = await fc_info.value
            await chooser.set_files(str(video))
            return True
        except Exception:
            pass
    return False


async def _try_set_files_on_input(
    page,
    video: Path,
    *,
    chooser_labels: tuple[str, ...] = (),
    attempts: int = 90,
) -> bool:
    if chooser_labels and await _set_files_via_chooser(page, video, chooser_labels):
        return True
    selectors = (
        "input[type='file'][accept*='video']",
        "input[type='file']",
    )
    for attempt in range(attempts):
        for sel in selectors:
            loc = page.locator(sel).first
            if await loc.count():
                try:
                    await loc.set_input_files(str(video), timeout=120_000)
                    return True
                except Exception:
                    pass
        if chooser_labels and await _set_files_via_chooser(page, video, chooser_labels):
            return True
        if attempt and attempt % 10 == 0:
            print(f"  等待 file input… ({attempt}s)", flush=True)
        await asyncio.sleep(1)
    return False


async def _set_files_on_input(page, video: Path, *, chooser_labels: tuple[str, ...] = ()) -> None:
    if not await _try_set_files_on_input(page, video, chooser_labels=chooser_labels):
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


def _ig_modal(page):
    return page.get_by_role("dialog").first


async def _click_labeled(page, label: str, *, root=None) -> bool:
    """IG 常用 div[role=button] 而非原生 button；root 可限定在弹窗内。"""
    scope = root or page
    try:
        exact = scope.get_by_text(label, exact=True).first
        if await exact.count() and await exact.is_visible():
            if await exact.get_attribute("aria-disabled") != "true":
                await exact.click(timeout=8000)
                return True
    except Exception:
        pass
    for sel in (
        f'button:has-text("{label}")',
        f'div[role="button"]:has-text("{label}")',
        f'span:has-text("{label}")',
    ):
        try:
            loc = scope.locator(sel).first
            if not await loc.count() or not await loc.is_visible():
                continue
            if await loc.get_attribute("aria-disabled") == "true":
                continue
            await loc.click(timeout=8000)
            return True
        except Exception:
            continue
    try:
        btn = scope.get_by_role("button", name=label, exact=False).first
        if await btn.count() and await btn.is_visible():
            if await btn.get_attribute("aria-disabled") != "true":
                await btn.click(timeout=8000)
                return True
    except Exception:
        pass
    return False


async def _click_ig_modal(page, label: str) -> bool:
    modal = _ig_modal(page)
    if not await modal.count():
        return False
    return await _click_labeled(page, label, root=modal)


def _video_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
    ).strip()
    return float(out)


def _prepare_ig_video(video: Path) -> tuple[Path, Path | None]:
    """IG Reels 上限约 90s，超长则截断。"""
    max_s = float(_env("US_IG_MAX_SECONDS", "89"))
    try:
        dur = _video_duration(video)
    except Exception:
        return video, None
    if dur <= max_s + 0.5:
        return video, None
    tmp = PROJECT_ROOT / "logs" / f".ig_trim_{video.stem}.mp4"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-t",
            str(int(max_s)),
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(tmp),
        ],
        check=True,
        capture_output=True,
    )
    print(f"  视频 {dur:.0f}s 超限，截断至 {int(max_s)}s", flush=True)
    return tmp, tmp


async def _ig_modal_headline(page) -> str:
    modal = _ig_modal(page)
    if not await modal.count():
        return ""
    try:
        return (
            await modal.evaluate("el => ((el.innerText || '').trim().split(/\\n/)[0] || '')")
        ).strip()
    except Exception:
        return ""


_IG_CAPTION_STEPS = frozenset({"Create", "New reel", "New post"})


async def _ig_on_caption_page(page) -> bool:
    step = await _ig_modal_headline(page)
    if step not in _IG_CAPTION_STEPS:
        return False
    modal = _ig_modal(page)
    cap = modal.locator(
        "div[aria-label*='caption' i], textarea[aria-label*='caption' i], div[role='textbox']"
    ).first
    share = modal.locator('div[role="button"]', has_text="Share").first
    return (await cap.count() > 0) or (await share.count() > 0)


async def _ig_advance_through_steps(page) -> None:
    """Crop → Edit → New reel/Create（配文页）。"""
    for i in range(60):
        if await _ig_on_caption_page(page):
            step = await _ig_modal_headline(page)
            print(f"  已进入配文页 ({step})", flush=True)
            return
        step = await _ig_modal_headline(page)
        if step in ("Crop", "Edit"):
            if await _click_ig_modal(page, "Next"):
                print(f"  {step} → Next", flush=True)
                await asyncio.sleep(3)
                continue
        if i and i % 10 == 0:
            print(f"  等待步骤… ({step or '无弹窗'}) {i}s", flush=True)
        await asyncio.sleep(1)
    shot = PROJECT_ROOT / "logs" / "ig_step_stuck.png"
    await page.screenshot(path=str(shot), full_page=True)
    raise SocialTestError(f"未进入配文页，截图: {shot}")


async def _ig_share_succeeded(page) -> bool:
    for text in (
        "Your reel has been shared",
        "Reel shared",
        "Your post has been shared",
        "Post shared",
    ):
        if await page.get_by_text(text, exact=False).count():
            print(f"  IG 确认: {text}", flush=True)
            return True
    return False


async def _click_ig_share_and_wait(page) -> None:
    for i in range(45):
        modal = _ig_modal(page)
        if not await modal.count():
            if await _ig_share_succeeded(page):
                return
            print("  发布弹窗已关闭", flush=True)
            return
        for err in (
            "Something went wrong",
            "Try again",
            "Video too long",
            "too long",
            "couldn't be shared",
        ):
            if await page.get_by_text(err, exact=False).count():
                shot = PROJECT_ROOT / "logs" / "ig_share_error.png"
                await page.screenshot(path=str(shot), full_page=True)
                raise SocialTestError(f"IG 报错: {err}，截图: {shot}")
        if await _ig_share_succeeded(page):
            await _click_ig_modal(page, "Done")
            return
        btn = modal.locator('div[role="button"]', has_text="Share").first
        if await btn.count() and await btn.is_visible():
            if await btn.get_attribute("aria-disabled") != "true":
                await btn.click(timeout=10_000)
                print("  已点击 Share", flush=True)
                break
        if i and i % 5 == 0:
            print(f"  等待 Share 可点… ({i * 2}s)", flush=True)
        await asyncio.sleep(2)
    else:
        shot = PROJECT_ROOT / "logs" / "test_instagram_fail.png"
        await page.screenshot(path=str(shot), full_page=True)
        raise SocialTestError(f"未能点击 Share，截图: {shot}")

    for i in range(60):
        if await _ig_share_succeeded(page):
            await _click_ig_modal(page, "Done")
            print("  发布处理完成", flush=True)
            return
        if not await _ig_modal(page).count():
            print("  发布弹窗已关闭", flush=True)
            return
        if i and i % 5 == 0:
            print(f"  等待 IG 上传完成… ({i * 2}s)", flush=True)
        await asyncio.sleep(2)
    shot = PROJECT_ROOT / "logs" / "ig_share_stuck.png"
    await page.screenshot(path=str(shot), full_page=True)
    print(f"  警告: 未看到成功提示，继续校验主页，截图: {shot}", flush=True)


async def _wait_click_next(page, *, rounds: int = 2, timeout_s: int = 120, ig_modal: bool = False) -> None:
    clicked = 0
    for _ in range(timeout_s):
        if clicked >= rounds:
            return
        hit = await _click_ig_modal(page, "Next") if ig_modal else await _click_labeled(page, "Next")
        if hit:
            clicked += 1
            print(f"  已点击 Next ({clicked}/{rounds})", flush=True)
            await asyncio.sleep(3)
            continue
        await asyncio.sleep(1)
    if clicked < rounds:
        print(f"  警告: 仅点到 {clicked}/{rounds} 次 Next，继续尝试配文", flush=True)


async def _fill_ig_caption(page, caption: str) -> None:
    short = caption[:2200]
    modal = _ig_modal(page)
    roots = [modal] if await modal.count() else [page]
    selectors = (
        "div[aria-label='Write a caption...']",
        "textarea[aria-label='Write a caption...']",
        "div[aria-label='Write a caption…']",
        "[contenteditable='true'][role='textbox']",
        "div[role='textbox']",
    )
    for root in roots:
        for sel in selectors:
            loc = root.locator(sel).first
            if not await loc.count():
                continue
            try:
                await loc.click(timeout=5000)
                await loc.fill(short)
                print("  已填写 caption", flush=True)
                return
            except Exception:
                try:
                    await loc.click(timeout=3000)
                    await page.keyboard.press("Meta+a")
                    await page.keyboard.type(short[:800])
                    print("  已键盘输入 caption", flush=True)
                    return
                except Exception:
                    pass


def _ig_username() -> str:
    return _env("US_IG_USERNAME", "zark_2023").lstrip("@")


async def _count_ig_media(page, username: str) -> tuple[int, int]:
    """返回 (reel_count, post_count)。"""
    reels = posts = 0
    for path, kind in ((f"/{username}/reels/", "reels"), (f"/{username}/", "posts")):
        await page.goto(
            f"https://www.instagram.com{path}",
            wait_until="domcontentloaded",
            timeout=90_000,
        )
        await asyncio.sleep(4)
        if kind == "reels":
            reels = await page.locator('a[href*="/reel/"]').count()
        else:
            posts = await page.locator('a[href*="/p/"]').count()
    return reels, posts


async def check_ig_profile() -> bool:
    """检查主页是否已有 reel/post。"""
    cookie = cookie_path("instagram")
    if not cookie.is_file():
        print("  ✗ 无 Instagram cookie", flush=True)
        return False
    user = _ig_username()
    _ensure_patchright()
    from patchright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(**_launch_kwargs(headed=False))
        context = await _new_context(browser, "instagram", headed=False)
        page = await context.new_page()
        try:
            reels, posts = await _count_ig_media(page, user)
            shot = PROJECT_ROOT / "logs" / "ig_profile_check.png"
            await page.screenshot(path=str(shot), full_page=True)
            print(f"  @{user} | reels={reels} posts={posts} | 截图: {shot}", flush=True)
            return reels > 0 or posts > 0
        finally:
            await context.close()
            await browser.close()


async def _verify_ig_published(page, *, before_reels: int = 0, before_posts: int = 0) -> bool:
    user = _ig_username()
    for text in (
        "Your reel has been shared",
        "Your post has been shared",
        "Reel shared",
        "Post shared",
    ):
        try:
            if await page.get_by_text(text, exact=False).count() > 0:
                pass
        except Exception:
            pass
    try:
        reels, posts = await _count_ig_media(page, user)
        if reels > before_reels or posts > before_posts:
            print(f"  校验: @{user} reels {before_reels}→{reels}, posts {before_posts}→{posts}", flush=True)
            return True
        print(f"  校验失败: @{user} 仍 reels={reels} posts={posts}", flush=True)
    except Exception as exc:
        print(f"  校验异常: {exc}", flush=True)
    return False


async def _ig_click_sidebar_post(page) -> bool:
    """点击左侧 Create 下拉里的 Post（避免误点 feed 里的 Post 字样）。"""
    for i in range(await page.locator('a[role="link"]').count()):
        link = page.locator('a[role="link"]').nth(i)
        try:
            if (await link.inner_text()).strip() != "Post":
                continue
            if not await link.is_visible():
                continue
            box = await link.bounding_box()
            if not box or box["x"] > 280:
                continue
            await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            return True
        except Exception:
            continue
    return False


async def _ig_open_create_post(page) -> None:
    """桌面端 Create 先出下拉菜单（Post / Live video / Ad），需点侧边栏 Post 链接。"""
    create = page.locator('svg[aria-label="New post"], svg[aria-label="Create"]').first
    await create.click(timeout=8000)
    await asyncio.sleep(1.5)

    if not await _ig_click_sidebar_post(page):
        raise SocialTestError("未能点击 Create 菜单里的 Post")

    for i in range(20):
        if await page.locator("input[type='file']").count() > 0:
            print("  已点击 Create → Post（上传对话框已打开）", flush=True)
            return
        await asyncio.sleep(1)
    shot = PROJECT_ROOT / "logs" / "ig_create_no_input.png"
    await page.screenshot(path=str(shot), full_page=True)
    raise SocialTestError(f"点击 Post 后未出现上传对话框，截图: {shot}")


async def _ig_select_reel_mode(page) -> None:
    """上传弹窗内若有 Reel 标签则切换（部分 UI 版本）。"""
    await asyncio.sleep(1)
    for label in ("Reel", "Reels"):
        if await _click_labeled(page, label):
            print(f"  已选择 {label} 模式", flush=True)
            await asyncio.sleep(1)
            return
    try:
        tab = page.get_by_role("tab", name="Reel", exact=False).first
        if await tab.count() and await tab.is_visible():
            await tab.click(timeout=3000)
            print("  已点击 Reel 标签", flush=True)
    except Exception:
        pass


async def publish_instagram(page, video: Path, caption: str, *, assist: bool) -> None:
    user = _ig_username()
    reels0, posts0 = await _count_ig_media(page, user)
    print(f"  发布前 @{user}: reels={reels0} posts={posts0}", flush=True)

    upload_video, trim_tmp = _prepare_ig_video(video)

    await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=90_000)
    await _dismiss_common(page)
    await asyncio.sleep(2)

    if "login" in page.url.lower():
        raise SocialTestError("Instagram 未登录")

    await _ig_open_create_post(page)
    await _ig_select_reel_mode(page)
    for name in ("Select from computer", "Select from Computer", "Select video"):
        await _click_if_visible(page, role="button", name=name)
    await _set_files_on_input(
        page,
        upload_video,
        chooser_labels=("Select from computer", "Select from Computer", "Select video"),
    )
    print("  已选择视频，等待处理…", flush=True)

    for i in range(90):
        if await _click_ig_modal(page, "OK"):
            print("  已确认 OK 弹窗", flush=True)
            break
        step = await _ig_modal_headline(page)
        if step in ("Crop", "Edit", *_IG_CAPTION_STEPS):
            print(f"  上传完成，当前步骤: {step}", flush=True)
            break
        if i and i % 15 == 0:
            print(f"  视频处理中… ({i}s)", flush=True)
        await asyncio.sleep(1)

    await _ig_advance_through_steps(page)
    await _fill_ig_caption(page, caption)

    if assist:
        shot = PROJECT_ROOT / "logs" / "test_instagram_assist.png"
        await page.screenshot(path=str(shot), full_page=True)
        print(f"  [assist] 已填好，请手动点 Share。截图: {shot}", flush=True)
        await asyncio.sleep(120)
        if not await _verify_ig_published(page, before_reels=reels0, before_posts=posts0):
            raise SocialTestError("assist 模式未检测到发布成功")
        return

    await _click_ig_share_and_wait(page)

    for i in range(40):
        if await _verify_ig_published(page, before_reels=reels0, before_posts=posts0):
            print("  ✓ Instagram 发布成功（主页 reel/post 已增加）", flush=True)
            if trim_tmp and trim_tmp.is_file():
                trim_tmp.unlink(missing_ok=True)
            return
        if i and i % 5 == 0:
            print(f"  等待主页更新… ({i * 3}s)", flush=True)
        await asyncio.sleep(3)

    shot = PROJECT_ROOT / "logs" / "test_instagram_fail.png"
    await page.screenshot(path=str(shot), full_page=True)
    raise SocialTestError(f"已点 Share 但主页无新内容，截图: {shot}")


def _fb_reels_panel(page):
    return page.locator("div").filter(has_text="Reels").filter(has=page.locator("text=继续")).first


async def _fb_click_continue(page) -> bool:
    panel = _fb_reels_panel(page)
    if await panel.count():
        if await _click_labeled(page, "继续", root=panel):
            return True
        if await _click_labeled(page, "Continue", root=panel):
            return True
    return await _click_labeled(page, "继续") or await _click_labeled(page, "Continue")


async def _fb_profile_id(page) -> str:
    await page.goto("https://www.facebook.com/me", wait_until="domcontentloaded", timeout=120_000)
    await asyncio.sleep(3)
    if "profile.php?id=" in page.url:
        return page.url.split("profile.php?id=")[1].split("&")[0]
    return ""


async def _count_fb_reels(page) -> int:
    try:
        profile_id = await _fb_profile_id(page)
        if profile_id:
            await page.goto(
                f"https://www.facebook.com/profile.php?id={profile_id}&sk=reels_tab",
                wait_until="domcontentloaded",
                timeout=120_000,
            )
        else:
            for tab in ("Reels", "Reels 视频", "视频"):
                loc = page.get_by_role("tab", name=tab, exact=False).first
                if await loc.count():
                    await loc.click(timeout=5000)
                    await asyncio.sleep(3)
                    break
        await asyncio.sleep(4)
        for empty in (
            "你还没创建任何 Reels",
            "You haven't created any Reels",
            "尚无 Reels",
            "No reels yet",
        ):
            if await page.get_by_text(empty, exact=False).count():
                return 0
        main = page.locator('[role="main"]').first
        scope = main if await main.count() else page
        own = await scope.locator('a[href*="/reel/"][href*="fb_shorts_profile"]').count()
        if own:
            return own
        if profile_id:
            owner = await scope.locator(
                f'a[href*="profile.php?id={profile_id}"][href*="owner_reels"]'
            ).count()
            if owner:
                return max(own, 1)
        return 0
    except Exception:
        return 0


async def _verify_fb_published(page, *, before: int = 0) -> bool:
    count = await _count_fb_reels(page)
    if count > before:
        print(f"  校验: Reels {before}→{count}", flush=True)
        return True
    print(f"  校验失败: 仍 Reels={count}", flush=True)
    return False


async def check_fb_profile() -> bool:
    cookie = cookie_path("facebook")
    if not cookie.is_file():
        print("  ✗ 无 Facebook cookie", flush=True)
        return False
    _ensure_patchright()
    from patchright.async_api import async_playwright

    async with async_playwright() as p:
        context, browser = await _open_publish_context(p, "facebook", headed=False)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            count = await _count_fb_reels(page)
            shot = PROJECT_ROOT / "logs" / "fb_profile_check.png"
            await page.screenshot(path=str(shot), full_page=True)
            print(f"  Reels={count} | 截图: {shot}", flush=True)
            return count > 0
        finally:
            await context.close()
            if browser is not None:
                await browser.close()


async def _fill_fb_reel_caption(page, caption: str) -> None:
    short = caption[:2200]
    for sel in (
        "textarea[placeholder*='Reels']",
        "div[placeholder*='Reels']",
        "div[aria-label*='Describe your reel']",
        "div[role='textbox']",
        "[contenteditable='true']",
    ):
        loc = page.locator(sel).first
        if not await loc.count():
            continue
        try:
            await loc.click(timeout=5000)
            await loc.fill(short)
            print("  已填写 Reels 描述", flush=True)
            return
        except Exception:
            pass
    for ph in ("描述一下你的 Reels", "Describe your reel", "描述"):
        loc = page.get_by_placeholder(ph, exact=False).first
        if await loc.count():
            try:
                await loc.fill(short)
                print("  已填写 Reels 描述", flush=True)
                return
            except Exception:
                pass


async def _click_fb_publish(page) -> None:
    for label in ("发布", "Publish", "发帖", "Post", "Share", "分享"):
        try:
            btn = page.get_by_role("button", name=label, exact=True).first
            if await btn.count() and await btn.is_visible():
                if await btn.get_attribute("aria-disabled") != "true":
                    await btn.click(timeout=10_000)
                    print(f"  已点击 {label}", flush=True)
                    return
        except Exception:
            pass
    panel = page.locator("div").filter(has_text="Reels 设置").first
    if await panel.count():
        for label in ("发布", "Publish"):
            if await _click_labeled(page, label, root=panel):
                print(f"  已点击 {label}", flush=True)
                return
    shot = PROJECT_ROOT / "logs" / "test_facebook_fail.png"
    await page.screenshot(path=str(shot), full_page=True)
    raise SocialTestError(f"未能点击发布，截图: {shot}")


async def _wait_fb_upload_complete(page, *, timeout_s: int = 240) -> None:
    """发布中期间留在当前页，至少等待上传完成。"""
    publish_started = asyncio.get_event_loop().time()
    min_wait_s = 45
    saw_publishing = False
    for i in range(timeout_s):
        elapsed = asyncio.get_event_loop().time() - publish_started
        if await page.get_by_text("发布中", exact=False).count():
            saw_publishing = True
            if i % 10 == 0:
                print(f"  Facebook 发布中… ({int(elapsed)}s)", flush=True)
            await asyncio.sleep(1)
            continue
        for err in ("发布失败", "出了点问题", "Something went wrong", "Try again"):
            if await page.get_by_text(err, exact=False).count():
                shot = PROJECT_ROOT / "logs" / "test_facebook_fail.png"
                await page.screenshot(path=str(shot), full_page=True)
                raise SocialTestError(f"Facebook 报错: {err}，截图: {shot}")
        for ok in ("已发布", "发布成功", "Reel shared", "Your reel is live", "Reels 已发布"):
            if await page.get_by_text(ok, exact=False).count():
                print(f"  Facebook 确认: {ok}", flush=True)
                return
        if saw_publishing and elapsed >= min_wait_s:
            if not await page.get_by_text("Reels 设置", exact=False).count():
                print(f"  发布界面已关闭（等待 {int(elapsed)}s）", flush=True)
                return
            if not await page.get_by_text("发布中", exact=False).count() and elapsed >= 60:
                print(f"  发布中已结束（等待 {int(elapsed)}s）", flush=True)
                return
        await asyncio.sleep(1)
    shot = PROJECT_ROOT / "logs" / "test_facebook_fail.png"
    await page.screenshot(path=str(shot), full_page=True)
    print(f"  警告: 发布等待超时，截图: {shot}", flush=True)


async def publish_facebook(page, video: Path, caption: str, *, assist: bool) -> None:
    reels0 = await _count_fb_reels(page)
    print(f"  发布前 Reels={reels0}", flush=True)

    upload_video, trim_tmp = _prepare_ig_video(video)

    chooser_labels = ("添加视频", "Add video", "Add Video", "上传")
    selected = False
    for nav_round in range(3):
        await page.goto("https://www.facebook.com/reels/create", wait_until="domcontentloaded", timeout=120_000)
        await _dismiss_common(page)
        await asyncio.sleep(5)
        if "login" in page.url.lower():
            raise SocialTestError("Facebook 未登录")
        if await _set_files_via_chooser(page, upload_video, chooser_labels):
            selected = True
            break
        if await _try_set_files_on_input(page, upload_video, chooser_labels=chooser_labels, attempts=20):
            selected = True
            break
        print(f"  未找到上传入口，重新加载创建页（第 {nav_round + 1} 次）…", flush=True)
    if not selected:
        shot = PROJECT_ROOT / "logs" / "test_facebook_fail.png"
        await page.screenshot(path=str(shot), full_page=True)
        raise SocialTestError(f"未找到视频 file input，截图: {shot}")
    print("  已选择视频", flush=True)
    await asyncio.sleep(8)

    if not await _fb_click_continue(page):
        raise SocialTestError("未能点击第一次「继续」")
    print("  预览 → 继续", flush=True)
    await asyncio.sleep(6)

    await _fill_fb_reel_caption(page, caption)

    if assist:
        shot = PROJECT_ROOT / "logs" / "test_facebook_assist.png"
        await page.screenshot(path=str(shot), full_page=True)
        print(f"  [assist] 请手动点「继续」→「发布」。截图: {shot}", flush=True)
        await asyncio.sleep(120)
        if not await _verify_fb_published(page, before=reels0):
            raise SocialTestError("assist 模式未检测到 Facebook 发布成功")
        return

    if not await _fb_click_continue(page):
        raise SocialTestError("未能点击第二次「继续」")
    print("  编辑 → 继续", flush=True)
    await asyncio.sleep(5)

    await _click_fb_publish(page)
    await _wait_fb_upload_complete(page)

    for i in range(30):
        if await _verify_fb_published(page, before=reels0):
            print("  ✓ Facebook Reels 发布成功", flush=True)
            if trim_tmp and trim_tmp.is_file():
                trim_tmp.unlink(missing_ok=True)
            return
        if i and i % 3 == 0:
            print(f"  等待主页 Reels 更新… ({i * 5}s)", flush=True)
        await asyncio.sleep(5)

    shot = PROJECT_ROOT / "logs" / "test_facebook_fail.png"
    await page.screenshot(path=str(shot), full_page=True)
    raise SocialTestError(f"已点发布但 Reels 未增加，截图: {shot}")


_LI_NEXT_LABELS = ("下一步", "Next", "下一页")
_LI_POST_LABELS = ("发布", "Post", "投稿", "投稿する", "公開", "分享")


async def _li_btn_label(loc) -> str:
    try:
        text = (await loc.inner_text()).strip()
        if text:
            return text
        return (await loc.get_attribute("aria-label") or "").strip()
    except Exception:
        return ""


def _li_label_is_next(label: str) -> bool:
    return any(name in label for name in _LI_NEXT_LABELS)


def _li_label_is_post(label: str) -> bool:
    return any(name in label for name in _LI_POST_LABELS) and not _li_label_is_next(label)


async def _li_on_compose_page(page) -> bool:
    if await _loc_count(page, ".share-creation-state") > 0:
        return True
    if await _loc_count(page, "div.ql-editor") > 0:
        return True
    if await _loc_count(page, "div.share-creation-state__text-editor") > 0:
        return True
    return False


async def _li_in_edit_modal(page) -> bool:
    if await page.get_by_text("编辑", exact=True).count():
        return True
    loc = page.locator("button.share-actions__primary-action").first
    if await loc.count() and await loc.is_visible():
        if _li_label_is_next(await _li_btn_label(loc)):
            return True
    for name in _LI_NEXT_LABELS:
        try:
            btn = page.get_by_role("button", name=name, exact=True).first
            if await btn.count() and await btn.is_visible():
                return True
        except Exception:
            pass
    return False


async def _li_click_next_step(page) -> bool:
    dialog = page.get_by_role("dialog").first
    roots = [dialog] if await dialog.count() else [page]
    for root in roots:
        loc = root.locator("button.share-actions__primary-action").first
        if await loc.count() and await loc.is_visible():
            label = await _li_btn_label(loc)
            if _li_label_is_next(label):
                if await loc.get_attribute("disabled"):
                    continue
                if await loc.get_attribute("aria-disabled") == "true":
                    continue
                await loc.click(timeout=8000)
                return True
        for name in _LI_NEXT_LABELS:
            try:
                btn = root.get_by_role("button", name=name, exact=True).first
                if await btn.count() and await btn.is_visible():
                    if await btn.get_attribute("disabled"):
                        continue
                    await btn.click(timeout=8000)
                    return True
            except Exception:
                pass
            if await _click_labeled(page, name, root=root):
                return True
    return False


async def _li_post_button_ready(page) -> bool:
    if not await _li_on_compose_page(page):
        return False
    loc = page.locator("button.share-actions__primary-action").first
    if await loc.count() and await loc.is_visible():
        if await loc.get_attribute("disabled"):
            return False
        if await loc.get_attribute("aria-disabled") == "true":
            return False
        if _li_label_is_post(await _li_btn_label(loc)):
            return True
    for name in _LI_POST_LABELS:
        try:
            btn = page.get_by_role("button", name=name, exact=True).first
            if await btn.count() and await btn.is_visible():
                if await btn.get_attribute("disabled"):
                    continue
                if _li_label_is_post(await _li_btn_label(btn)):
                    return True
        except Exception:
            pass
    return False


async def _click_li_post(page) -> bool:
    loc = page.locator("button.share-actions__primary-action").first
    if await loc.count() and await loc.is_visible():
        label = await _li_btn_label(loc)
        if _li_label_is_post(label):
            if not await loc.get_attribute("disabled") and await loc.get_attribute("aria-disabled") != "true":
                await loc.click(timeout=8000)
                return True
    for name in _LI_POST_LABELS:
        try:
            btn = page.get_by_role("button", name=name, exact=True).first
            if await btn.count() and await btn.is_visible():
                if await btn.get_attribute("disabled"):
                    continue
                if _li_label_is_post(await _li_btn_label(btn)):
                    await btn.click(timeout=8000)
                    return True
        except Exception:
            pass
    for label in _LI_POST_LABELS:
        if await _click_labeled(page, label):
            return True
    return False


async def _li_is_background_uploading(page) -> bool:
    """仅凭明确的上传横幅文案判断；进度条元素会误判，已弃用。"""
    try:
        body = (await page.locator("body").inner_text())[:3000]
        markers = (
            "正在上传",
            "保持页面打开状态以完成上传",
            "Uploading",
            "Keep this page open",
            "keep the page open",
        )
        return any(m in body for m in markers)
    except Exception:
        return False


async def _li_wait_background_upload(page, *, timeout_s: int = 180) -> None:
    """点发布后 LinkedIn 会在 feed 后台传视频；离开页面会中断上传。"""
    for i in range(timeout_s // 2):
        if not await _li_is_background_uploading(page):
            if i:
                print(f"  后台上传完成 ({i * 2}s)", flush=True)
            return
        if i and i % 5 == 0:
            print(f"  后台上传中，保持页面… ({i * 2}s)", flush=True)
        await asyncio.sleep(2)
    print(f"  后台上传等待超时 ({timeout_s}s)，继续校验", flush=True)


def _li_caption_needle(caption: str) -> str:
    """从 caption 取一段足够独特的文字，用于在动态页核对本次帖子。"""
    first = ""
    for line in caption.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            first = line
            break
    if not first:
        first = caption.strip()
    # 去掉结尾标点，截一段长度适中的片段
    snippet = first[:50].strip().rstrip("?.!，。！？")
    return snippet


async def _verify_linkedin_published(page, needle: str = "") -> bool:
    try:
        await page.goto(
            "https://www.linkedin.com/in/me/recent-activity/all/",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        await asyncio.sleep(4)
        body = (await page.locator("body").inner_text())[:6000]
        empty = (
            "目前没有可查看的内容",
            "No activity yet",
            "Nothing to see for now",
            "尚无可查看的内容",
            "表示するコンテンツがありません",
            "閲覧できるコンテンツはありません",
        )
        if any(m in body for m in empty):
            return False
        # 必须能在动态页找到本次 caption 的独特片段，避免把旧帖当成功
        if needle:
            return needle in body
        # 无 needle 时退回：有动态列表即算（仅兼容旧调用）
        if await _loc_count(page, "div.feed-shared-update-v2") > 0:
            return True
        return False
    except Exception:
        return False


async def _wait_li_feed_ready(page, *, timeout_s: int = 45) -> None:
    markers = ("发动态", "Start a post", "视频", "Video")
    for _ in range(timeout_s):
        for marker in markers:
            try:
                if await page.get_by_text(marker, exact=False).count():
                    return
            except Exception:
                pass
        await asyncio.sleep(1)
    raise SocialTestError("LinkedIn feed 未加载完成")


async def _li_select_video(page, upload_video: Path) -> None:
    """Feed 上点「视频」或打开发帖框后选文件；headless 下 chooser 失败时回退 file input。"""
    await _wait_li_feed_ready(page)

    for label in ("视频", "Video", "動画"):
        try:
            loc = page.get_by_role("button", name=label, exact=True).first
            if await loc.count() and await loc.is_visible():
                try:
                    async with page.expect_file_chooser(timeout=8000) as fc_info:
                        await loc.click(timeout=5000)
                    chooser = await fc_info.value
                    await chooser.set_files(str(upload_video))
                    print(f"  已通过 {label} 选择视频", flush=True)
                    return
                except Exception:
                    await loc.click(timeout=5000)
                    await asyncio.sleep(2)
                    if await _try_set_files_on_input(page, upload_video, attempts=25):
                        print(f"  已通过 {label} + file input 选择视频", flush=True)
                        return
        except Exception:
            pass
        if await _click_labeled(page, label):
            await asyncio.sleep(2)
            if await _try_set_files_on_input(page, upload_video, attempts=25):
                print(f"  已通过 {label}(labeled) 选择视频", flush=True)
                return

    opened = False
    for text in ("发动态", "Start a post", "投稿を開始"):
        try:
            loc = page.get_by_placeholder(text, exact=False).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=5000)
                print(f"  已点击发帖框: {text}", flush=True)
                opened = True
                break
        except Exception:
            if await _click_labeled(page, text):
                print(f"  已点击: {text}", flush=True)
                opened = True
                break
    if not opened:
        for sel in (
            "button.share-box-feed-entry__trigger",
            ".share-box-feed-entry__trigger",
        ):
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=5000)
                    print("  已点击 share-box 发帖入口", flush=True)
                    opened = True
                    break
            except Exception:
                pass
    await asyncio.sleep(2)
    chooser_labels = ("Add a video", "添加视频", "動画を追加", "Upload video", "视频", "Video")
    for label in ("视频", "Video", "動画"):
        await _click_labeled(page, label)
    if await _try_set_files_on_input(
        page, upload_video, chooser_labels=chooser_labels, attempts=90
    ):
        print("  已通过 composer 选择视频", flush=True)
        return
    raise SocialTestError("未找到视频 file input")


async def publish_linkedin(page, video: Path, caption: str, *, assist: bool) -> None:
    await page.goto(
        "https://www.linkedin.com/feed/",
        wait_until="domcontentloaded",
        timeout=120_000,
    )
    await _dismiss_common(page)
    await asyncio.sleep(3)

    url = page.url.lower()
    if "login" in url or "authwall" in url or "checkpoint" in url:
        print("  检测到登录页，请在浏览器完成登录（最多等 3 分钟）…", flush=True)
        if not await _wait_logged_in(page, "linkedin", timeout_s=180):
            raise SocialTestError(
                "LinkedIn 未登录，请 ./scripts/test-us-social.sh login linkedin --manual"
            )
        await page.goto(
            "https://www.linkedin.com/feed/",
            wait_until="domcontentloaded",
            timeout=120_000,
        )
        await asyncio.sleep(2)

    upload_video, trim_tmp = _prepare_ig_video(video)
    await _li_select_video(page, upload_video)
    print("  已选择视频，等待处理…", flush=True)

    ready = False
    for i in range(60):
        if await _li_in_edit_modal(page):
            if await _li_click_next_step(page):
                print("  向导 → 下一步", flush=True)
                await asyncio.sleep(3)
                continue
        if await _li_post_button_ready(page):
            ready = True
            print(f"  可发布 ({i * 2}s)", flush=True)
            break
        if i and i % 5 == 0:
            state = "编辑向导" if await _li_in_edit_modal(page) else "处理中"
            print(f"  {state}… ({i * 2}s)", flush=True)
        await asyncio.sleep(2)
    if not ready:
        while await _li_in_edit_modal(page) and await _li_click_next_step(page):
            print("  向导 → 下一步（补点）", flush=True)
            await asyncio.sleep(3)
        if await _li_post_button_ready(page):
            ready = True
    if not ready:
        shot = PROJECT_ROOT / "logs" / "test_linkedin_fail.png"
        await page.screenshot(path=str(shot), full_page=True)
        raise SocialTestError(f"未能进入发布页，截图: {shot}")

    for sel in (
        "div.ql-editor",
        "div.share-creation-state__text-editor div.ql-editor",
        "div[role='textbox']",
        ".share-creation-state__text-editor div[contenteditable='true']",
    ):
        loc = page.locator(sel).first
        if await loc.count():
            try:
                await loc.click(timeout=5000)
                await loc.fill(caption[:3000])
                print("  已填写正文", flush=True)
                break
            except Exception:
                try:
                    await loc.click(timeout=3000)
                    await page.keyboard.type(caption[:500])
                    print("  已键盘输入正文", flush=True)
                    break
                except Exception:
                    pass

    needle = _li_caption_needle(caption)

    if assist:
        shot = PROJECT_ROOT / "logs" / "test_linkedin_assist.png"
        await page.screenshot(path=str(shot), full_page=True)
        print(f"  [assist] 请手动点「发布」。截图: {shot}", flush=True)
        await asyncio.sleep(120)
        if not await _verify_linkedin_published(page, needle):
            raise SocialTestError("assist 模式未检测到 LinkedIn 发布成功")
        return

    if not await _click_li_post(page):
        shot = PROJECT_ROOT / "logs" / "test_linkedin_fail.png"
        await page.screenshot(path=str(shot), full_page=True)
        raise SocialTestError(f"未能点击「发布/Post」，截图: {shot}")
    print("  已点击发布", flush=True)

    # 点发布后横幅是异步出现的：先等它出现（最多 20s），再等后台上传结束。
    # 期间务必停留在当前页，离开会中断上传。
    for _ in range(10):
        if await _li_is_background_uploading(page):
            print("  检测到后台上传横幅", flush=True)
            break
        await asyncio.sleep(2)
    await _li_wait_background_upload(page, timeout_s=240)
    await asyncio.sleep(6)

    # 必须核对本次 caption 片段出现在动态页，避免把旧帖当成功
    for i in range(40):
        if await _verify_linkedin_published(page, needle):
            print("  ✓ LinkedIn 发布成功（已校验本次帖子）", flush=True)
            if trim_tmp and trim_tmp.is_file():
                trim_tmp.unlink(missing_ok=True)
            return
        if i and i % 5 == 0:
            print(f"  等待本次动态出现… ({i * 4}s)", flush=True)
        await asyncio.sleep(4)

    shot = PROJECT_ROOT / "logs" / "test_linkedin_fail.png"
    await page.screenshot(path=str(shot), full_page=True)
    raise SocialTestError(
        f"已点发布但动态页未出现本次帖子（needle={needle!r}），截图: {shot}"
    )


async def _open_publish_context(p, platform: str, *, headed: bool):
    """Instagram 用 cookie（与 check 一致）；其余平台优先 persistent profile。"""
    launch = _launch_kwargs(headed=headed)
    if platform == "instagram":
        browser = await p.chromium.launch(**launch)
        context = await _new_context(browser, platform, headed=headed)
        return context, browser

    prof = profile_dir(platform)
    use_persistent = prof.is_dir() and any(prof.iterdir())

    if use_persistent:
        kw: dict = {
            "headless": not headed,
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "viewport": {"width": 1440, "height": 900},
            "args": launch.get("args", []),
        }
        if launch.get("executable_path"):
            kw["executable_path"] = launch["executable_path"]
        else:
            kw["channel"] = launch.get("channel", "chrome")
        context = await p.chromium.launch_persistent_context(str(prof), **kw)
        try:
            home = str(sau_home())
            if home not in sys.path:
                sys.path.insert(0, home)
            from utils.base_social_media import set_init_script

            context = await set_init_script(context)
        except Exception:
            pass
        return context, None

    browser = await p.chromium.launch(**launch)
    context = await _new_context(browser, platform, headed=headed)
    return context, browser


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
        context, browser = await _open_publish_context(
            p, platform, headed=headed or assist
        )
        page = context.pages[0] if context.pages else await context.new_page()
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
            if browser is not None:
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

    p_prof = sub.add_parser("profile", help="检查主页是否已有内容（instagram / facebook）")
    p_prof.add_argument("platform", choices=["instagram", "facebook"])

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

    if args.cmd == "profile":
        if args.platform == "instagram":
            ok = asyncio.run(check_ig_profile())
        else:
            ok = asyncio.run(check_fb_profile())
        return 0 if ok else 1

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
