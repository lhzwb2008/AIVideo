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
    _dismiss_common,
    _launch_kwargs,
    _set_files_via_chooser,
    profile_dir,
    sau_home,
)

VIDEO = ROOT / "archive/published/20260609/en/20260609_094757.mp4"
LOGS = ROOT / "logs"
URLS = (
    "https://business.facebook.com/latest/reels_composer",
    "https://www.facebook.com/",
)


async def try_upload(page, tag: str) -> None:
    await page.screenshot(path=str(LOGS / f"fb_comp_{tag}_0.png"), full_page=True)
    labels = (
        "照片/视频", "Photo/video", "添加视频", "Add video", "添加照片/视频",
        "创建 Reels", "Create a reel", "Reel", "上传视频",
    )
    for label in labels:
        if await _set_files_via_chooser(page, VIDEO, (label,)):
            print(f"  chooser via {label!r} ok", flush=True)
            await asyncio.sleep(10)
            await page.screenshot(path=str(LOGS / f"fb_comp_{tag}_1.png"), full_page=True)
            btns = await page.evaluate("""() => [...new Set(
                [...document.querySelectorAll('button, div[role=button]')]
                .map(e => (e.innerText||'').trim()).filter(t => t && t.length < 30)
            )]""")
            print(f"  buttons={btns[:25]}", flush=True)
            return
    # click composer then retry
    for text in ("分享你的新鲜事", "What's on your mind", "创建帖子"):
        loc = page.get_by_text(text, exact=False).first
        if await loc.count():
            await loc.click(timeout=5000)
            await asyncio.sleep(2)
            break
    for label in labels:
        if await _set_files_via_chooser(page, VIDEO, (label,)):
            print(f"  after composer, chooser via {label!r} ok", flush=True)
            await asyncio.sleep(10)
            await page.screenshot(path=str(LOGS / f"fb_comp_{tag}_2.png"), full_page=True)
            return
    print("  no chooser worked", flush=True)


async def main():
    from patchright.async_api import async_playwright
    sys.path.insert(0, str(sau_home()))
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
        for url in URLS:
            print(f"\n=== {url} ===", flush=True)
            await page.goto(url, wait_until="networkidle", timeout=120000)
            await _dismiss_common(page)
            await asyncio.sleep(5)
            print("url=", page.url, flush=True)
            await try_upload(page, url.split("/")[-1] or "biz")
        await ctx.close()

asyncio.run(main())
