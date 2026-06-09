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
        await asyncio.sleep(4)
        pid = ""
        if "profile.php?id=" in page.url:
            pid = page.url.split("profile.php?id=")[1].split("&")[0]
        print("profile", page.url, "id", pid)
        if pid:
            await page.goto(f"https://www.facebook.com/profile.php?id={pid}&sk=reels_tab", wait_until="domcontentloaded", timeout=120000)
            await asyncio.sleep(5)
        body = (await page.locator("body").inner_text())[:2000]
        print("body snippet:", body[:800])
        links = await page.evaluate("""() => [...document.querySelectorAll('a[href]')].map(a => a.href).filter(h => h.includes('reel') || h.includes('video')).slice(0,20)""")
        print("reel/video links:", links)
        await page.screenshot(path=str(ROOT/"logs/fb_profile_dump.png"), full_page=True)
        await ctx.close()
asyncio.run(main())
