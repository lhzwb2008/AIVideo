#!/usr/bin/env python3
"""快速探测 Facebook Reels 发布 UI（不点发布）。"""
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
    _new_context,
    profile_dir,
    sau_home,
)

LOGS = ROOT / "logs"
URLS = (
    "https://www.facebook.com/reels/create",
    "https://www.facebook.com/reels/",
    "https://www.facebook.com/",
)


async def dump(page, tag: str) -> None:
    texts = await page.evaluate("""() => {
        const out = [];
        for (const el of document.querySelectorAll('button, a, div[role=button], span, input[type=file]')) {
            const t = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim();
            if (t && t.length < 80) out.push(t);
        }
        return [...new Set(out)].slice(0, 60);
    }""")
    fc = await page.locator("input[type='file']").count()
    path = LOGS / f"fb_diag_{tag}.png"
    await page.screenshot(path=str(path), full_page=True)
    print(f"[{tag}] url={page.url}", flush=True)
    print(f"  file_inputs={fc}", flush=True)
    print(f"  labels={texts[:40]}", flush=True)


async def main() -> None:
    from patchright.async_api import async_playwright

    home = sau_home()
    if str(home) not in sys.path:
        sys.path.insert(0, str(home))
    try:
        from utils.base_social_media import set_init_script
    except Exception:
        set_init_script = None

    async with async_playwright() as p:
        prof = profile_dir("facebook")
        launch = _launch_kwargs(headed=False)
        kw = {
            "headless": True,
            "locale": "en-US",
            "timezone_id": "America/New_York",
            "viewport": {"width": 1440, "height": 900},
            "args": launch.get("args", []),
        }
        if launch.get("proxy"):
            kw["proxy"] = launch["proxy"]
        if launch.get("executable_path"):
            kw["executable_path"] = launch["executable_path"]
        else:
            kw["channel"] = launch.get("channel", "chrome")
        context = await p.chromium.launch_persistent_context(str(prof), **kw)
        if set_init_script:
            context = await set_init_script(context)
        page = context.pages[0] if context.pages else await context.new_page()
        try:
            for url in URLS:
                await page.goto(url, wait_until="domcontentloaded", timeout=120000)
                await _dismiss_common(page)
                await asyncio.sleep(4)
                tag = url.split("/")[-1] or "home"
                await dump(page, tag)
        finally:
            await context.close()


if __name__ == "__main__":
    asyncio.run(main())
