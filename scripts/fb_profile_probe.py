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
        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=120000)
        await _dismiss_common(page)
        await page.wait_for_selector("text=Reels", timeout=60000)
        await asyncio.sleep(3)

        # 打开个人主页
        for sel in ('a[aria-label*="个人主页"]', 'a[href*="/profile.php"]', 'a[href*="/me/"]'):
            loc = page.locator(sel).first
            if await loc.count():
                href = await loc.get_attribute("href")
                print("profile link", sel, href)
                if href:
                    await page.goto("https://www.facebook.com" + href if href.startswith("/") else href, timeout=120000)
                    break
        await asyncio.sleep(5)
        print("url", page.url)
        await page.screenshot(path=str(ROOT / "logs/fb_profile_0.png"), full_page=True)

        for tab in ("Reels", "Reels 视频", "快拍"):
            loc = page.get_by_role("tab", name=tab, exact=False).first
            if await loc.count():
                await loc.click()
                print("tab", tab)
                await asyncio.sleep(4)
                break

        texts = await page.evaluate("""() => [...new Set(
            [...document.querySelectorAll('button, a, div[role=button], span')]
            .map(e => (e.innerText||e.getAttribute('aria-label')||'').trim())
            .filter(t => t && t.length < 50)
        )].slice(0,40)""")
        print("labels:", texts)
        await page.screenshot(path=str(ROOT / "logs/fb_profile_reels.png"), full_page=True)
        await ctx.close()

if __name__ == "__main__":
    asyncio.run(main())
