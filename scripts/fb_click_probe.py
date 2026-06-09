#!/usr/bin/env python3
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from test_us_social_publish import _dismiss_common, _launch_kwargs, _set_files_via_chooser, profile_dir, sau_home

VIDEO = ROOT / "archive/published/20260609/en/20260609_094757.mp4"

async def main():
    sys.path.insert(0, str(sau_home()))
    from utils.base_social_media import set_init_script
    from patchright.async_api import async_playwright

    async with async_playwright() as p:
        prof = profile_dir("facebook")
        launch = _launch_kwargs(headed=False)
        kw = {
            "headless": False,
            "viewport": {"width": 1920, "height": 1080},
            "args": launch.get("args", []),
        }
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
        await asyncio.sleep(8)

        clicked = await page.evaluate("""() => {
            for (const el of document.querySelectorAll('span, div[role=button], a')) {
                const t = (el.innerText || '').trim();
                if (t === '照片/视频' || t === 'Photo/video') {
                    const r = el.getBoundingClientRect();
                    if (r.width > 5 && r.height > 5) {
                        el.click();
                        return {ok: true, text: t, x: r.x, y: r.y};
                    }
                }
            }
            return {ok: false};
        }""")
        print("photo click:", clicked)

        ok = await _set_files_via_chooser(page, VIDEO, ("照片/视频", "Photo/video"))
        print("chooser:", ok)
        if not ok:
            async with page.expect_file_chooser(timeout=8000) as fc:
                await page.evaluate("""() => {
                    const inp = document.querySelector("input[type=file]");
                    if (inp) inp.click();
                }""")
            ch = await fc.value
            await ch.set_files(str(VIDEO))
            print("input chooser ok")

        await asyncio.sleep(15)
        print("dialogs", await page.get_by_role("dialog").count())
        d = page.get_by_role("dialog").first
        if await d.count():
            print("dialog:", (await d.inner_text())[:400])
        await page.screenshot(path=str(ROOT / "logs/fb_click_result.png"), full_page=True)
        await ctx.close()

asyncio.run(main())
