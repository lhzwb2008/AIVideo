#!/usr/bin/env python3
import asyncio, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
home = ROOT / "vendor/social-auto-upload"
for sub in (home / ".venv/lib").iterdir():
    if sub.name.startswith("python"):
        sys.path.insert(0, str(sub / "site-packages")); break
from test_us_social_publish import (  # noqa: E402
    _click_ig_modal,
    _dismiss_common,
    _ig_modal,
    _ig_open_create_post,
    _launch_kwargs,
    _new_context,
    _set_files_on_input,
)
VIDEO = ROOT / "archive/published/20260609/en/20260609_094757.mp4"

async def main():
    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(**_launch_kwargs(headed=True))
        context = await _new_context(browser, "instagram", headed=True)
        page = await context.new_page()
        await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=90000)
        await _dismiss_common(page); await asyncio.sleep(2)
        await _ig_open_create_post(page)
        await _set_files_on_input(page, VIDEO, chooser_labels=("Select from computer",))
        await asyncio.sleep(5)
        roles = await page.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll('[role]')) {
                const r = el.getAttribute('role');
                const t = (el.innerText||'').slice(0,40);
                if (['dialog','presentation'].includes(r) && t.includes('Crop') || r==='dialog')
                    out.push({role:r, text:t.slice(0,80), tag:el.tagName});
            }
            return out.slice(0,15);
        }""")
        print("roles:", roles)
        print("dialog count", await page.locator('div[role="dialog"]').count())
        modal = page.get_by_role("dialog").first
        inner = await modal.evaluate("""el => {
            const out = [];
            for (const e of el.querySelectorAll('button, div[role=button], span, a')) {
                const t = (e.innerText||e.getAttribute('aria-label')||'').trim();
                if (t === 'Next' || t === 'Share' || t === 'Crop')
                    out.push({tag:e.tagName, role:e.getAttribute('role'), text:t, disabled:e.getAttribute('aria-disabled')});
            }
            return out;
        }""")
        print("modal inner Next candidates:", inner)
        for sel in ('button:has-text("Next")', 'div[role="button"]:has-text("Next")', 'span:has-text("Next")'):
            loc = modal.locator(sel)
            print(sel, await loc.count(), await loc.first.is_visible() if await loc.count() else False)
        print("modal next click", await _click_ig_modal(page, "Next"))
        await asyncio.sleep(3)
        await page.screenshot(path=str(ROOT/"logs/ig_dialog_after_next.png"))
        await context.close(); await browser.close()
asyncio.run(main())
