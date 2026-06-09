#!/usr/bin/env python3
"""快速查 LinkedIn 最近动态，确认视频是否真发出去了。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from test_us_social_publish import (  # noqa: E402
    _dismiss_common,
    _launch_kwargs,
    profile_dir,
    sau_home,
)


async def main() -> None:
    sys.path.insert(0, str(sau_home()))
    from utils.base_social_media import set_init_script
    from patchright.async_api import async_playwright

    async with async_playwright() as p:
        prof = profile_dir("linkedin")
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
        await page.goto(
            "https://www.linkedin.com/in/me/recent-activity/all/",
            wait_until="domcontentloaded",
            timeout=120000,
        )
        await _dismiss_common(page)
        await asyncio.sleep(6)
        await page.screenshot(path=str(ROOT / "logs/li_diag.png"), full_page=True)
        body = (await page.locator("body").inner_text()).replace("\n", " ")
        print("URL:", page.url, flush=True)
        print("ACTIVITY[:600]:", body[:600], flush=True)
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
