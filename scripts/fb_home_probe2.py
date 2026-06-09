#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from test_us_social_publish import (
    _dismiss_common,
    _launch_kwargs,
    _set_files_via_chooser,
    profile_dir,
    sau_home,
)

VIDEO = ROOT / "archive/published/20260609/en/20260609_094757.mp4"


async def click_by_text(page, text: str) -> bool:
    loc = page.get_by_text(text, exact=True).first
    if not await loc.count():
        return False
    try:
        await loc.scroll_into_view_if_needed()
        box = await loc.bounding_box()
        if box:
            await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            return True
    except Exception:
        pass
    return False


async def main():
    home = sau_home()
    sys.path.insert(0, str(home))
    from utils.base_social_media import set_init_script
    from patchright.async_api import async_playwright

    async with async_playwright() as p:
        prof = profile_dir("facebook")
        launch = _launch_kwargs(headed=False)
        kw = {"headless": True, "viewport": {"width": 1440, "height": 900}, "args": launch.get("args", [])}
        if launch.get("proxy"):
            kw["proxy"] = launch["proxy"]
        if launch.get("executable_path"):
            kw["executable_path"] = launch["executable_path"]
        else:
            kw["channel"] = launch.get("channel", "chrome")
        ctx = await p.chromium.launch_persistent_context(str(prof), **kw)
        ctx = await set_init_script(ctx)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=120000)
        await _dismiss_common(page)
        await asyncio.sleep(5)
        await page.screenshot(path=str(ROOT / "logs/fb_home2_0.png"), full_page=True)

        for label in ("照片/视频", "Photo/video"):
            if await click_by_text(page, label):
                print("clicked text", label)
                await asyncio.sleep(2)
                break

        if await _set_files_via_chooser(page, VIDEO, ("照片/视频", "Photo/video", "添加照片/视频")):
            print("chooser upload ok")
        else:
            print("chooser failed, try hidden input")
            inp = page.locator("input[type='file']").first
            if await inp.count():
                await inp.set_input_files(str(VIDEO))
                print("hidden input ok")

        await asyncio.sleep(15)
        await page.screenshot(path=str(ROOT / "logs/fb_home2_1.png"), full_page=True)
        dialogs = await page.get_by_role("dialog").count()
        print("dialogs=", dialogs)
        if dialogs:
            inner = await page.get_by_role("dialog").first.inner_text()
            print("dialog head:", inner[:300].replace("\n", " | "))
        await ctx.close()

if __name__ == "__main__":
    asyncio.run(main())
