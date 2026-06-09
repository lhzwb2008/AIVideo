#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from test_us_social_publish import _dismiss_common, _launch_kwargs, profile_dir, sau_home

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
        await page.goto("https://business.facebook.com/latest/reels_composer", wait_until="domcontentloaded", timeout=120000)
        await _dismiss_common(page)
        await asyncio.sleep(10)
        texts = await page.locator("button, a, div[role='button'], span").all_inner_texts()
        uniq = []
        for t in texts:
            t = t.strip()
            if t and len(t) < 60 and t not in uniq:
                uniq.append(t)
        print("url:", page.url)
        for t in uniq[:50]:
            print(" -", repr(t))
        print("file inputs:", await page.locator("input[type='file']").count())
        await page.screenshot(path=str(ROOT / "logs/fb_biz_composer.png"), full_page=True)
        await ctx.close()

if __name__ == "__main__":
    asyncio.run(main())
