#!/usr/bin/env python3
import asyncio, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))
from test_us_social_publish import _dismiss_common, _launch_kwargs, _new_context, _set_files_via_chooser

VIDEO = ROOT / "archive/published/20260609/en/20260609_094757.mp4"

async def main():
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(**_launch_kwargs(headed=False))
        context = await _new_context(browser, "facebook", headed=False)
        page = await context.new_page()
        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=120000)
        await _dismiss_common(page)
        await asyncio.sleep(8)
        body = await page.evaluate("() => (document.body.innerText||'').slice(0,600)")
        print("body:", body[:400])
        print("files:", await page.locator("input[type='file']").count())
        ok = await _set_files_via_chooser(page, VIDEO, ("照片/视频", "Photo/video"))
        print("chooser:", ok)
        await asyncio.sleep(12)
        print("dialogs:", await page.get_by_role("dialog").count())
        if await page.get_by_role("dialog").count():
            print((await page.get_by_role("dialog").first.inner_text())[:300])
        await page.screenshot(path=str(ROOT/"logs/fb_cookie_result.png"), full_page=True)
        await context.close(); await browser.close()
asyncio.run(main())
