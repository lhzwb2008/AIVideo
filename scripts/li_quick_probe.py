#!/usr/bin/env python3
import asyncio, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))
from test_us_social_publish import _dismiss_common, _launch_kwargs, profile_dir, sau_home

VIDEO = ROOT / "archive/published/20260609/en/20260609_094757.mp4"

async def main():
    sys.path.insert(0, str(sau_home()))
    from utils.base_social_media import set_init_script
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        prof = profile_dir("linkedin")
        launch = _launch_kwargs(headed=False)
        kw = {"headless": True, "viewport": {"width": 1440, "height": 900}, "args": launch.get("args", [])}
        if launch.get("proxy"): kw["proxy"] = launch["proxy"]
        if launch.get("executable_path"): kw["executable_path"] = launch["executable_path"]
        else: kw["channel"] = launch.get("channel", "chrome")
        ctx = await p.chromium.launch_persistent_context(str(prof), **kw)
        ctx = await set_init_script(ctx)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=120000)
        await _dismiss_common(page)
        await asyncio.sleep(5)
        texts = await page.evaluate("""() => [...new Set(
            [...document.querySelectorAll('button, div[role=button], span, a')]
            .map(e => (e.innerText||e.getAttribute('aria-label')||'').trim())
            .filter(t => t && t.length < 60)
        )].slice(0,50)""")
        print("url", page.url)
        for t in texts: print(" -", repr(t))
        print("share-box", await page.locator("button.share-box-feed-entry__trigger").count())
        await page.screenshot(path=str(ROOT/"logs/li_feed_probe.png"), full_page=True)
        await ctx.close()
asyncio.run(main())
