#!/usr/bin/env python3
import asyncio, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))
from test_us_social_publish import (
    _click_if_visible, _click_labeled, _dismiss_common, _launch_kwargs,
    _set_files_on_input, _set_files_via_chooser, profile_dir, sau_home,
)
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
        for text in ("发动态", "Start a post", "投稿を開始", "新动态"):
            loc = page.get_by_text(text, exact=False).first
            if await loc.count():
                try:
                    await loc.click(timeout=5000)
                    print("opened", text)
                    break
                except Exception as e:
                    print("open fail", text, e)
        await asyncio.sleep(3)
        await page.screenshot(path=str(ROOT/"logs/li_open.png"), full_page=True)
        for text in ("视频", "Video", "動画"):
            if await _click_labeled(page, text):
                print("clicked", text)
                break
        await asyncio.sleep(2)
        ok = await _set_files_via_chooser(page, VIDEO, ("添加视频", "Add a video", "Upload video", "视频"))
        if not ok:
            await _set_files_on_input(page, VIDEO, chooser_labels=("添加视频", "Add a video"))
        print("upload attempted")
        await asyncio.sleep(30)
        await page.screenshot(path=str(ROOT/"logs/li_uploaded.png"), full_page=True)
        btns = await page.evaluate("""() => [...new Set(
            [...document.querySelectorAll('button, div[role=button]')]
            .map(e => (e.innerText||e.getAttribute('aria-label')||'').trim())
            .filter(t => t && t.length < 30)
        )]""")
        print("buttons:", [b for b in btns if any(k in b for k in ('发布','Post','投稿','公开','分享'))])
        await ctx.close()
asyncio.run(main())
