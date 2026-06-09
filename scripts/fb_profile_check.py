#!/usr/bin/env python3
import asyncio, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))
from test_us_social_publish import _dismiss_common, _launch_kwargs, profile_dir, sau_home

async def main():
    sys.path.insert(0, str(sau_home()))
    from utils.base_social_media import set_init_script
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        prof = profile_dir("facebook")
        launch = _launch_kwargs(headed=False)
        kw = {"headless": True, "viewport": {"width": 1920, "height": 1080}, "args": launch.get("args", [])}
        if launch.get("proxy"): kw["proxy"] = launch["proxy"]
        if launch.get("executable_path"): kw["executable_path"] = launch["executable_path"]
        else: kw["channel"] = launch.get("channel", "chrome")
        ctx = await p.chromium.launch_persistent_context(str(prof), **kw)
        ctx = await set_init_script(ctx)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://www.facebook.com/me", wait_until="domcontentloaded", timeout=120000)
        await _dismiss_common(page)
        await asyncio.sleep(6)
        print("url", page.url)
        for tab in ("Reels", "Reels 视频", "视频"):
            t = page.get_by_role("tab", name=tab, exact=False).first
            if await t.count():
                await t.click(); await asyncio.sleep(4); print("tab", tab); break
        reels = await page.locator('a[href*="/reel/"]').count()
        print("reel links:", reels)
        await page.screenshot(path=str(ROOT/"logs/fb_profile_check.png"), full_page=True)
        await ctx.close()
asyncio.run(main())
