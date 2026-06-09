#!/usr/bin/env python3
"""一次性探测 Instagram Create 弹窗 UI。"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from test_us_social_publish import (  # noqa: E402
    _click_if_visible,
    _dismiss_common,
    _launch_kwargs,
    _new_context,
    cookie_path,
    sau_home,
)


async def main() -> None:
    home = sau_home()
    venv_site = home / ".venv" / "lib"
    if venv_site.is_dir():
        for sub in venv_site.iterdir():
            if sub.name.startswith("python"):
                sys.path.insert(0, str(sub / "site-packages"))
                break
    from patchright.async_api import async_playwright

    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(**_launch_kwargs(headed=True))
        context = await _new_context(browser, "instagram", headed=True)
        page = await context.new_page()
        await page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=90_000)
        await _dismiss_common(page)
        await asyncio.sleep(3)
        await page.screenshot(path=str(logs / "ig_probe_0_home.png"), full_page=True)

        from test_us_social_publish import _ig_open_create_post, _set_files_on_input

        await _ig_open_create_post(page)
        await page.screenshot(path=str(logs / "ig_probe_1_post_dialog.png"), full_page=True)

        # dump visible buttons / links text
        texts = await page.evaluate(
            """() => {
            const out = [];
            for (const el of document.querySelectorAll('button, a, div[role="button"], span, [role="tab"]')) {
                const t = (el.innerText || el.getAttribute('aria-label') || '').trim();
                if (t && t.length < 80) out.push(t);
            }
            return [...new Set(out)].slice(0, 80);
        }"""
        )
        print("visible labels:", texts)

        file_count = await page.locator("input[type='file']").count()
        print(f"file inputs after Post: {file_count}")

        for label in ("Select from computer", "Select from Computer", "Drag photos"):
            loc = page.get_by_text(label, exact=False)
            n = await loc.count()
            print(f"  text '{label}': {n}")

        video = ROOT / "archive/published/20260609/en/20260609_094757.mp4"
        if video.is_file():
            try:
                await _set_files_on_input(
                    page,
                    video,
                    chooser_labels=("Select from computer", "Select from Computer"),
                )
                print("upload ok")
                await asyncio.sleep(5)
                await page.screenshot(path=str(logs / "ig_probe_2_uploaded.png"), full_page=True)
            except Exception as exc:
                print(f"upload fail: {exc}")

        await asyncio.sleep(10)
        await context.close()
        await browser.close()


if __name__ == "__main__":
    if not cookie_path("instagram").is_file():
        print("no cookie")
        sys.exit(1)
    asyncio.run(main())
