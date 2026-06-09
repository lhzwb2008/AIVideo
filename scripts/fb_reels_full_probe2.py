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


def _fb_reels_panel(page):
    return page.locator("div").filter(has_text="Reels").filter(has=page.locator("text=继续")).first


async def click_continue(page) -> bool:
    panel = _fb_reels_panel(page)
    if await panel.count():
        return await _click_labeled(page, "继续", root=panel)
    return await _click_labeled(page, "继续")


async def main():
    upload, _ = _prepare_ig_video(VIDEO)
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
        await click_continue(page)
        print("continue 1")
        await asyncio.sleep(8)

        cap = page.get_by_placeholder("描述一下你的 Reels", exact=False).first
        if not await cap.count():
            cap = page.locator("div[role='textbox'], textarea").filter(has_text="").first
        if await cap.count():
            await cap.click()
            await cap.fill("Why Are Chips Wall Street's Wildest Ride? #stocks #markets")
            print("caption ok")

        await page.screenshot(path=str(ROOT / "logs/fb_full2_edit.png"), full_page=True)
        await click_continue(page)
        print("continue 2")
        await asyncio.sleep(8)
        await page.screenshot(path=str(ROOT / "logs/fb_full2_final.png"), full_page=True)

        for label in ("发布", "发帖", "Post", "Share", "分享", "Publish", "立即分享"):
            if await _click_labeled(page, label):
                print("publish", label)
                break

        texts = await page.evaluate("""() => [...new Set(
            [...document.querySelectorAll('button, div[role=button], span')]
            .map(e => (e.innerText||'').trim()).filter(t => t && t.length < 30)
        )]""")
        print("final buttons:", [t for t in texts if any(k in t for k in ('发布','发帖','Post','Share','分享','继续','Continue'))])
        await ctx.close()

asyncio.run(main())
