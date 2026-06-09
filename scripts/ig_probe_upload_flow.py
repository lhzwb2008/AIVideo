#!/usr/bin/env python3
"""上传视频后观察 IG 弹窗按钮。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
home = ROOT / "vendor" / "social-auto-upload"
for sub in (home / ".venv" / "lib").iterdir() if (home / ".venv" / "lib").is_dir() else []:
    if sub.name.startswith("python"):
        sys.path.insert(0, str(sub / "site-packages"))
        break

from test_us_social_publish import (  # noqa: E402
    _dismiss_common,
    _ig_open_create_post,
    _launch_kwargs,
    _new_context,
    _set_files_on_input,
)

VIDEO = ROOT / "archive/published/20260609/en/20260609_094757.mp4"

async def dump_buttons(page, tag: str):
    texts = await page.evaluate("""() => {
        const out = [];
        for (const el of document.querySelectorAll('button, div[role="button"], [role="tab"]')) {
            const t = (el.innerText || el.getAttribute('aria-label') || '').trim();
            if (t && t.length < 60) out.push(t);
        }
        return [...new Set(out)];
    }""")
    print(f"[{tag}] buttons:", texts[:25])
    await page.screenshot(path=str(ROOT / "logs" / f"ig_upload_flow_{tag}.png"))

async def main():
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(**_launch_kwargs(headed=True))
        context = await _new_context(browser, "instagram", headed=True)
        page = await context.new_page()
        await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=90000)
        await _dismiss_common(page)
        await asyncio.sleep(2)
        await _ig_open_create_post(page)
        await _set_files_on_input(page, VIDEO, chooser_labels=("Select from computer",))
        print("uploaded")
        for wait, tag in ((3, "t3"), (7, "t10"), (10, "t20"), (20, "t40"), (20, "t60")):
            await asyncio.sleep(wait)
            await dump_buttons(page, tag)
        await context.close()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
