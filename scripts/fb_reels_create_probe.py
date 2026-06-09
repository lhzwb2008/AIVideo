#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from test_us_social_publish import (
    _dismiss_common,
    _launch_kwargs,
    _set_files_on_input,
    _set_files_via_chooser,
    profile_dir,
    sau_home,
)

VIDEO = ROOT / "archive/published/20260609/en/20260609_094757.mp4"


async def main():
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
        await asyncio.sleep(6)
        print("url:", page.url)
        await page.screenshot(path=str(ROOT / "logs/fb_reels_create_0.png"), full_page=True)

        for label in ("添加视频", "Add video", "上传", "创建 Reels"):
            if await _set_files_via_chooser(page, VIDEO, (label,)):
                print("chooser ok via", label)
                break
        else:
            await _set_files_on_input(page, VIDEO, chooser_labels=("添加视频", "Add video"))
            print("file input ok")

        await asyncio.sleep(15)
        await page.screenshot(path=str(ROOT / "logs/fb_reels_create_1.png"), full_page=True)
        btns = await page.evaluate("""() => [...new Set(
            [...document.querySelectorAll('button, div[role=button], span')]
            .map(e => (e.innerText||'').trim()).filter(t => t && t.length < 30)
        )].slice(0,35)""")
        print("buttons:", btns)
        await ctx.close()

if __name__ == "__main__":
    asyncio.run(main())
