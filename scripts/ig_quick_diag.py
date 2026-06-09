#!/usr/bin/env python3
"""快速诊断 IG 发布到哪一步（约 60–90s，不点 Share）。"""
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
    _ig_advance_through_steps,
    _ig_modal_headline,
    _ig_open_create_post,
    _launch_kwargs,
    _new_context,
    _prepare_ig_video,
    _set_files_on_input,
)

VIDEO = ROOT / "archive/published/20260609/en/20260609_094757.mp4"
LOGS = ROOT / "logs"


async def snap(page, name: str) -> None:
    step = await _ig_modal_headline(page)
    path = LOGS / f"ig_diag_{name}.png"
    await page.screenshot(path=str(path), full_page=True)
    print(f"  [{name}] step={step!r} → {path}", flush=True)


async def main() -> None:
    from patchright.async_api import async_playwright

    upload, _ = _prepare_ig_video(VIDEO)
    async with async_playwright() as p:
        browser = await p.chromium.launch(**_launch_kwargs(headed=False))
        context = await _new_context(browser, "instagram", headed=False)
        page = await context.new_page()
        try:
            await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=90000)
            await _dismiss_common(page)
            await asyncio.sleep(2)
            await _ig_open_create_post(page)
            await _set_files_on_input(page, upload, chooser_labels=("Select from computer",))
            print("uploaded", flush=True)
            for i in range(60):
                step = await _ig_modal_headline(page)
                if step in ("Crop", "Edit", "Create"):
                    print(f"ready at {step}", flush=True)
                    break
                await asyncio.sleep(1)
            await snap(page, "before_advance")
            await _ig_advance_through_steps(page)
            await snap(page, "after_advance")
            modal = page.get_by_role("dialog").first
            share = modal.locator('div[role="button"]', has_text="Share").first
            cap = modal.locator("div[aria-label*='caption' i], div[role='textbox']").first
            print(
                f"  caption={await cap.count()} share={await share.count()} "
                f"share_disabled={await share.get_attribute('aria-disabled') if await share.count() else 'n/a'}",
                flush=True,
            )
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
