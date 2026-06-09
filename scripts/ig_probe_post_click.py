#!/usr/bin/env python3
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
    _launch_kwargs,
    _new_context,
    cookie_path,
)

async def main():
    from patchright.async_api import async_playwright
    logs = ROOT / "logs"
    async with async_playwright() as p:
        browser = await p.chromium.launch(**_launch_kwargs(headed=True))
        context = await _new_context(browser, "instagram", headed=True)
        page = await context.new_page()
        await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=90000)
        await _dismiss_common(page)
        await asyncio.sleep(2)

        create = page.locator('svg[aria-label="New post"], svg[aria-label="Create"]').first
        await create.click(timeout=5000)
        await asyncio.sleep(1.5)
        await page.screenshot(path=str(logs / "ig_post_click_0_menu.png"))

        candidates = await page.evaluate("""() => {
            const out = [];
            for (const el of document.querySelectorAll('a, button, div[role="button"], span, div')) {
                const t = (el.innerText || '').trim();
                if (t === 'Post' || t === 'Live video' || t === 'Ad') {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0)
                        out.push({tag: el.tagName, role: el.getAttribute('role'), text: t,
                                  x: r.x, y: r.y, w: r.width, h: r.height,
                                  cls: (el.className||'').slice(0,60)});
                }
            }
            return out;
        }""")
        print("Post menu candidates:", candidates)

        # try click smallest visible Post (menu item, not feed text)
        posts = [c for c in candidates if c['text'] == 'Post']
        if posts:
            # menu item is usually narrow width in sidebar area (x < 300)
            menu = sorted([c for c in posts if c['x'] < 400], key=lambda c: c['y'])
            target = menu[0] if menu else posts[0]
            print("clicking at", target)
            await page.mouse.click(target['x'] + target['w']/2, target['y'] + target['h']/2)
            await asyncio.sleep(3)
            await page.screenshot(path=str(logs / "ig_post_click_1_modal.png"))
            fc = await page.locator("input[type='file']").count()
            print("file inputs:", fc)
            texts = await page.evaluate("""() => [...new Set(
                [...document.querySelectorAll('button, span, div[role=button]')]
                .map(e => (e.innerText||'').trim()).filter(t => t && t.length < 40)
            )].slice(0,30)""")
            print("modal texts:", texts)
        await asyncio.sleep(15)
        await context.close()
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
