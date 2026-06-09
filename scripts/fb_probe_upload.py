#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
home = ROOT / "vendor/social-auto-upload"
for sub in (home / ".venv/lib").iterdir():
    if sub.name.startswith("python"):
        sys.path.insert(0, str(sub / "site-packages"))
        break

from test_us_social_publish import (  # noqa: E402
    _click_if_visible,
    _click_labeled,
    _dismiss_common,
    _launch_kwargs,
    _set_files_on_input,
    profile_dir,
    sau_home,
)

VIDEO = ROOT / "archive/published/20260609/en/20260609_094757.mp4"
LOGS = ROOT / "logs"


async def snap(page, tag):
    await page.screenshot(path=str(LOGS / f"fb_probe_{tag}.png"), full_page=True)
    fc = await page.locator("input[type='file']").count()
    print(f"{tag}: files={fc} url={page.url[:80]}", flush=True)


async def main():
    from patchright.async_api import async_playwright
    home = sau_home()
    sys.path.insert(0, str(home))
    from utils.base_social_media import set_init_script

    async with async_playwright() as p:
        prof = profile_dir("facebook")
        launch = _launch_kwargs(headed=False)
        kw = {"headless": True, "viewport": {"width": 1440, "height": 900}, "args": launch.get("args", [])}
        if launch.get("proxy"):
            kw["proxy"] = launch["proxy"]
        if launch.get("executable_path"):
            kw["executable_path"] = launch["executable_path"]
        else:
            kw["channel"] = "chrome"
        ctx = await p.chromium.launch_persistent_context(str(prof), **kw)
        ctx = await set_init_script(ctx)
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=120000)
        await _dismiss_common(page)
        await asyncio.sleep(3)
        await snap(page, "0_home")

        for label in ("照片/视频", "Photo/video", "照片", "视频", "Reels"):
            if await _click_labeled(page, label):
                print(f"clicked {label}", flush=True)
                await asyncio.sleep(2)
                await snap(page, f"1_{label}")
                break

        try:
            await _set_files_on_input(
                page, VIDEO,
                chooser_labels=("添加视频", "Add video", "添加照片/视频", "Add photos/videos", "照片/视频", "选择文件"),
            )
            print("upload ok", flush=True)
            await asyncio.sleep(8)
            await snap(page, "2_uploaded")
            texts = await page.evaluate("""() => [...new Set(
                [...document.querySelectorAll('button, div[role=button]')]
                .map(e => (e.innerText||'').trim()).filter(t => t && t.length < 40)
            )].slice(0,30)""")
            print("buttons:", texts, flush=True)
        except Exception as exc:
            print(f"upload fail: {exc}", flush=True)
            await snap(page, "2_fail")

        await ctx.close()

asyncio.run(main())
