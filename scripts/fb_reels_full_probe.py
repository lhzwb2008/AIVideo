#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from test_us_social_publish import (
    _click_labeled,
    _dismiss_common,
    _launch_kwargs,
    _prepare_ig_video,
    _set_files_via_chooser,
    profile_dir,
    sau_home,
)

VIDEO = ROOT / "archive/published/20260609/en/20260609_094757.mp4"


async def snap(page, tag):
    await page.screenshot(path=str(ROOT / f"logs/fb_full_{tag}.png"), full_page=True)
    btns = await page.evaluate("""() => [...new Set(
        [...document.querySelectorAll('button, div[role=button], span, textarea, div[role=textbox]')]
        .map(e => (e.innerText||e.getAttribute('aria-label')||e.getAttribute('placeholder')||'').trim())
        .filter(t => t && t.length < 40)
    )].slice(0,40)""")
    print(f"[{tag}]", btns[:25], flush=True)


async def main():
    upload, _ = _prepare_ig_video(VIDEO)  # trim to 89s, reuse IG helper
    sys.path.insert(0, str(sau_home()))
    from utils.base_social_media import set_init_script
    from patchright.async_api import async_playwright

    async with async_playwright() as p:
        prof = profile_dir("facebook")
        launch = _launch_kwargs(headed=False)
        kw = {"headless": True, "viewport": {"width": 1920, "height": 1080}, "args": launch.get("args", [])}
        if launch.get("proxy"):
            kw["proxy"] = launch["proxy"]
        if launch.get("executable_path"):
            kw["executable_path"] = launch["executable_path"]
        else:
            kw["channel"] = launch.get("channel", "chrome")
        ctx = await p.chromium.launch_persistent_context(str(prof), **kw)
        ctx = await set_init_script(ctx)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://www.facebook.com/reels/create", wait_until="domcontentloaded", timeout=120000)
        await _dismiss_common(page)
        await asyncio.sleep(5)
        await _set_files_via_chooser(page, upload, ("添加视频", "Add video"))
        await asyncio.sleep(10)
        await snap(page, "uploaded")
        for label in ("继续", "Continue", "Next", "下一步"):
            if await _click_labeled(page, label):
                print("clicked", label)
                await asyncio.sleep(8)
                await snap(page, f"after_{label}")
                break
        for label in ("发布", "发帖", "Post", "Share", "分享", "Publish"):
            if await _click_labeled(page, label):
                print("clicked publish", label)
                await asyncio.sleep(8)
                await snap(page, f"after_{label}")
                break
        await ctx.close()

if __name__ == "__main__":
    asyncio.run(main())
