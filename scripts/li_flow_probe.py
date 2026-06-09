#!/usr/bin/env python3
"""LinkedIn 发布全流程探测：逐步截图 + 按钮文案。"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from test_us_social_publish import (  # noqa: E402
    _dismiss_common,
    _launch_kwargs,
    _li_btn_label,
    _li_click_next_step,
    _li_in_edit_modal,
    _li_on_compose_page,
    _li_post_button_ready,
    _prepare_ig_video,
    profile_dir,
    sau_home,
)

VIDEO = ROOT / "archive/published/20260609/en/20260609_094757.mp4"
SCRIPT = ROOT / "logs/en/last_script_20260609_094440_us04.json"


async def dump_buttons(page, tag: str) -> None:
    await page.screenshot(path=str(ROOT / f"logs/li_flow_{tag}.png"), full_page=True)
    primary = page.locator("button.share-actions__primary-action").first
    primary_label = ""
    if await primary.count():
        primary_label = await _li_btn_label(primary)
    print(
        f"[{tag}] url={page.url[:80]} "
        f"dialog={await page.get_by_role('dialog').count()} "
        f"compose={await _li_on_compose_page(page)} "
        f"edit={await _li_in_edit_modal(page)} "
        f"post_ready={await _li_post_button_ready(page)} "
        f"primary={primary_label!r}",
        flush=True,
    )


async def main() -> None:
    caption = ""
    if SCRIPT.is_file():
        data = json.loads(SCRIPT.read_text(encoding="utf-8"))
        caption = (data.get("tiktok_caption") or data.get("caption") or "")[:300]

    upload, _ = _prepare_ig_video(VIDEO)
    sys.path.insert(0, str(sau_home()))
    from utils.base_social_media import set_init_script
    from patchright.async_api import async_playwright

    async with async_playwright() as p:
        prof = profile_dir("linkedin")
        launch = _launch_kwargs(headed=False)
        kw = {
            "headless": True,
            "viewport": {"width": 1440, "height": 900},
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
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=120000)
        await _dismiss_common(page)
        await asyncio.sleep(4)
        loc = page.get_by_role("button", name="视频", exact=True).first
        async with page.expect_file_chooser(timeout=8000) as fc:
            await loc.click()
        await (await fc.value).set_files(str(upload))
        print("uploaded", flush=True)

        for step in range(8):
            await asyncio.sleep(3)
            await dump_buttons(page, f"step{step}")
            if await _li_post_button_ready(page):
                print("compose ready", flush=True)
                break
            if await _li_click_next_step(page):
                print(f"clicked next at step{step}", flush=True)
                continue
            await asyncio.sleep(5)

        for sel in ("div.ql-editor", "div[role='textbox']"):
            loc = page.locator(sel).first
            if await loc.count():
                await loc.click(timeout=5000)
                await loc.fill(caption or "Market Sketch test")
                print("filled caption", flush=True)
                break

        await dump_buttons(page, "before_post")
        primary = page.locator("button.share-actions__primary-action").first
        if await primary.count():
            print("clicking primary:", await _li_btn_label(primary), flush=True)
            await primary.click(timeout=8000)
        await asyncio.sleep(8)
        await dump_buttons(page, "after_post")
        await page.goto(
            "https://www.linkedin.com/in/me/recent-activity/all/",
            wait_until="domcontentloaded",
            timeout=120000,
        )
        await asyncio.sleep(5)
        await dump_buttons(page, "activity")
        await ctx.close()


if __name__ == "__main__":
    asyncio.run(main())
