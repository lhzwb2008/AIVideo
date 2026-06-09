#!/usr/bin/env python3
"""上传后 60s 内探测 LinkedIn 编辑器状态。"""
import asyncio, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts"))
from test_us_social_publish import _dismiss_common, _launch_kwargs, _prepare_ig_video, profile_dir, sau_home

VIDEO = ROOT / "archive/published/20260609/en/20260609_094757.mp4"

async def snap(page, tag):
    await page.screenshot(path=str(ROOT/f"logs/li_state_{tag}.png"), full_page=True)
    btns = await page.evaluate("""() => [...new Set(
        [...document.querySelectorAll('button, div[role=button]')]
        .map(e => ({
          t: (e.innerText||e.getAttribute('aria-label')||'').trim(),
          dis: e.disabled || e.getAttribute('aria-disabled')==='true'
        })).filter(x => x.t && x.t.length<40)
    )].slice(0,25)""")
    print(f"[{tag}] dialogs={await page.get_by_role('dialog').count()} share-state={await page.locator('.share-creation-state').count()}")
    print(f"  buttons:", btns)

async def main():
    upload, _ = _prepare_ig_video(VIDEO)
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
        await asyncio.sleep(4)
        loc = page.get_by_role("button", name="视频", exact=True).first
        async with page.expect_file_chooser(timeout=8000) as fc:
            await loc.click()
        await (await fc.value).set_files(str(upload))
        print("uploaded")
        for wait, tag in ((10,"t10"),(30,"t30"),(60,"t60")):
            await asyncio.sleep(wait if tag=="t10" else 20)
            await snap(page, tag)
        await ctx.close()
asyncio.run(main())
