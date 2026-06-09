#!/usr/bin/env python3
"""上传 + 点发布后，盯住后台上传横幅的真实文案/百分比。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from test_us_social_publish import (  # noqa: E402
    _dismiss_common,
    _launch_kwargs,
    _li_click_next_step,
    _li_in_edit_modal,
    _li_post_button_ready,
    _click_li_post,
    _prepare_ig_video,
    profile_dir,
    sau_home,
)

VIDEO = ROOT / "archive/published/20260609/en/20260609_094757.mp4"

JS_PROBE = """() => {
  const bars = [...document.querySelectorAll('[role=progressbar]')].map(b => ({
    vis: !!(b.offsetWidth||b.offsetHeight),
    now: b.getAttribute('aria-valuenow'),
    label: (b.getAttribute('aria-label')||'').slice(0,40),
  }));
  const body = document.body.innerText;
  const markers = ['正在上传','保持页面打开','Uploading','处理中','正在处理'];
  const hit = markers.filter(m => body.includes(m));
  return { bars, hit, dialog: document.querySelectorAll('[role=dialog]').length };
}"""


async def main() -> None:
    upload, _ = _prepare_ig_video(VIDEO)
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
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=120000)
        await _dismiss_common(page)
        await asyncio.sleep(4)
        loc = page.get_by_role("button", name="视频", exact=True).first
        async with page.expect_file_chooser(timeout=8000) as fc:
            await loc.click()
        await (await fc.value).set_files(str(upload))
        print("uploaded file", flush=True)

        for _ in range(40):
            if await _li_post_button_ready(page):
                break
            if await _li_in_edit_modal(page):
                await _li_click_next_step(page)
            await asyncio.sleep(2)
        await _click_li_post(page)
        print("clicked publish", flush=True)

        for i in range(60):
            info = await page.evaluate(JS_PROBE)
            print(f"[{i*3}s] dialog={info['dialog']} hit={info['hit']} bars={info['bars']}", flush=True)
            if not info["hit"]:
                print("=> banner gone", flush=True)
                break
            await asyncio.sleep(3)
        await page.goto(
            "https://www.linkedin.com/in/me/recent-activity/all/",
            wait_until="domcontentloaded",
            timeout=120000,
        )
        await asyncio.sleep(5)
        body = (await page.locator("body").inner_text())[:500]
        print("ACTIVITY:", body.replace("\n", " ")[:300], flush=True)
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
