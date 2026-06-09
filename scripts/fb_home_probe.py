#!/usr/bin/env python3
"""首页发帖弹窗：点可见 composer → 照片/视频 → 上传。"""
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

        # 点 composer 容器（不是不可见的 span）
        for sel in (
            "div[role='button']:has-text('分享你的新鲜事')",
            "div[aria-label*='分享你的新鲜事']",
            "div[role='button']:has-text('新鲜事')",
        ):
            loc = page.locator(sel).first
            if await loc.count():
                try:
                    await loc.click(timeout=5000)
                    print("clicked composer", sel)
                    break
                except Exception as e:
                    print("composer fail", sel, e)

        await asyncio.sleep(2)
        await page.screenshot(path=str(ROOT / "logs/fb_home_composer.png"), full_page=True)

        labels = ("照片/视频", "Photo/video", "添加照片/视频", "Add photos/videos")
        for label in labels:
            if await _set_files_via_chooser(page, VIDEO, (label,)):
                print("upload via", label)
                await asyncio.sleep(12)
                await page.screenshot(path=str(ROOT / "logs/fb_home_uploaded.png"), full_page=True)
                btns = await page.locator("button, div[role='button']").all_inner_texts()
                print("buttons:", [b.strip() for b in btns if b.strip()][:20])
                break
        await ctx.close()

if __name__ == "__main__":
    asyncio.run(main())
