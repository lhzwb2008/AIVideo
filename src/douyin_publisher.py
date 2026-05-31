"""抖音创作者平台发布（独立 Playwright，不依赖 sau 填表逻辑）。"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from paths import ROOT


class DouyinPublishError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def sau_home(root: Path | None = None) -> Path:
    from paths import ROOT

    root = root or ROOT
    custom = _env("SAU_HOME")
    if custom:
        return Path(custom).expanduser()
    return root / "vendor" / "social-auto-upload"


def resolve_playwright_python(root: Path | None = None) -> Path | None:
    venv_py = sau_home(root) / ".venv" / "bin" / "python3"
    if venv_py.is_file():
        return venv_py
    return None


def cookie_path(root: Path | None = None, account: str | None = None) -> Path:
    account = account or _env("SAU_DOUYIN_ACCOUNT", "main")
    path = sau_home(root) / "cookies" / f"douyin_{account}.json"
    if not path.is_file():
        raise DouyinPublishError(
            f"未找到 cookie: {path}\n请先运行: ./douyin-login.sh"
        )
    return path


def _chrome_path() -> str:
    for path in (
        _env("LOCAL_CHROME_PATH"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    ):
        if path and Path(path).is_file():
            return path
    return ""


def _ensure_patchright():
    home = sau_home()
    venv_site = home / ".venv" / "lib"
    if venv_site.is_dir():
        for sub in venv_site.iterdir():
            if sub.name.startswith("python"):
                sys.path.insert(0, str(sub / "site-packages"))
                break
    try:
        from patchright.async_api import async_playwright  # noqa: F401
    except ImportError as exc:
        raise DouyinPublishError(
            "未安装 patchright。请先运行: ./scripts/setup-sau.sh"
        ) from exc


async def _dismiss_overlays(page) -> None:
    for _ in range(5):
        clicked = False
        for text in ("我知道了", "知道了", "关闭"):
            btn = page.get_by_role("button", name=text, exact=True)
            count = await btn.count()
            for i in range(count):
                item = btn.nth(i)
                try:
                    if await item.is_visible():
                        await item.click(timeout=2000)
                        await asyncio.sleep(0.8)
                        clicked = True
                except Exception:
                    continue
        if not clicked:
            break


async def _goto(page, url: str) -> None:
    last_exc = None
    for wait_until in ("commit", "domcontentloaded"):
        try:
            await page.goto(url, wait_until=wait_until, timeout=90_000)
            if "creator.douyin.com" in page.url:
                return
        except Exception as exc:
            last_exc = exc
            if "creator.douyin.com" in page.url:
                return
    if last_exc:
        raise last_exc


async def _try_click_upload_entry(page) -> None:
    """新版上传页有时需先点入口才渲染 file input。"""
    for text in ("上传视频", "发布视频", "点击上传", "上传"):
        loc = page.get_by_text(text, exact=False).first
        if not await loc.count():
            continue
        try:
            if await loc.is_visible():
                await loc.click(timeout=3000)
                await asyncio.sleep(1)
                return
        except Exception:
            continue


async def _require_logged_in(page) -> None:
    url = page.url.lower()
    if "passport" in url or "/login" in url:
        raise DouyinPublishError("未登录或 cookie 已失效，请先运行: ./douyin-login.sh")
    for text in ("扫码登录", "手机号登录", "登录后即可"):
        loc = page.get_by_text(text, exact=False).first
        if await loc.count():
            try:
                if await loc.is_visible():
                    raise DouyinPublishError("未登录或 cookie 已失效，请先运行: ./douyin-login.sh")
            except DouyinPublishError:
                raise
            except Exception:
                pass


async def _wait_file_input(page, timeout_s: int = 180, root: Path | None = None):
    """等待上传页 file input。抖音 SPA 常需 10–20s 才渲染控件，且 cookie 半失效时会更慢。"""
    selectors = (
        "input.semi-upload-hidden-input",
        "input[type='file'][accept*='video']",
        "div[class^='container'] input[type='file']",
        "div[class^='upload-content'] input[class='upload-input']",
        "div[class^='upload-content'] input",
        "input[type='file']",
    )
    for i in range(timeout_s // 2):
        await _require_logged_in(page)
        await _dismiss_overlays(page)
        if i in (0, 3, 8, 15, 25):
            await _try_click_upload_entry(page)
        for sel in selectors:
            loc = page.locator(sel).first
            if not await loc.count():
                continue
            try:
                await loc.wait_for(state="attached", timeout=3000)
                return loc
            except Exception:
                continue
        # 兜底：点「点击上传」触发 filechooser（新版页面可能无稳定 input 选择器）
        if i >= 5:
            try:
                trigger = page.get_by_text("点击上传", exact=False).first
                if await trigger.count() and await trigger.is_visible():
                    async with page.expect_file_chooser(timeout=5000) as fc_info:
                        await trigger.click(timeout=4000)
                    chooser = await fc_info.value
                    # 返回一个可 set_input_files 的占位 locator（调用方会用 set_input_files）
                    class _ChooserProxy:
                        def __init__(self, ch):
                            self._ch = ch

                        async def set_input_files(self, path):
                            await self._ch.set_files(path)

                    return _ChooserProxy(chooser)
            except Exception:
                pass
        if i and i % 15 == 0:
            print(f"  等待上传控件… ({i * 2}s) url={page.url}", flush=True)
        await asyncio.sleep(2)

    shot = (root or ROOT) / "logs" / "douyin_upload_page_fail.png"
    shot.parent.mkdir(parents=True, exist_ok=True)
    try:
        await page.screenshot(path=str(shot), full_page=True, timeout=60_000)
        print(f"  截图: {shot}", flush=True)
    except Exception:
        pass
    raise DouyinPublishError(
        f"上传页未就绪（{page.url}）。若页面是登录页，请先运行: ./douyin-login.sh"
    )


async def _wait_publish_form(page, timeout_s: int = 180) -> None:
    for i in range(timeout_s):
        markers = (
            page.get_by_text("作品描述", exact=True),
            page.get_by_text("重新上传", exact=False),
            page.get_by_text("填写作品标题", exact=False),
            page.locator(".zone-container[contenteditable='true']").first,
            page.locator("div[class*='editor-comp-publish'][contenteditable='true']").first,
        )
        for marker in markers:
            if not await marker.count():
                continue
            try:
                if await marker.is_visible():
                    return
            except Exception:
                continue
        if i and i % 15 == 0:
            print(f"  等待发布表单… ({i}s)", flush=True)
        await asyncio.sleep(1)
    raise DouyinPublishError(f"未能进入发布表单: {page.url}")


async def _fill_form(page, title: str, desc: str, tags: list[str]) -> None:
    await _dismiss_overlays(page)
    title = title[:30]

    # 新版 UI：标题是独立的 input（placeholder 含“填写作品标题”），简介在下方 contenteditable。
    title_input = page.locator(
        'input[placeholder*="作品标题"], input[placeholder*="标题"]'
    ).first
    title_filled = False
    if await title_input.count():
        try:
            await title_input.wait_for(state="visible", timeout=15_000)
            await title_input.click()
            await page.keyboard.press("Meta+A")
            await page.keyboard.press("Backspace")
            await title_input.fill(title)
            title_filled = True
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ 标题输入框填写失败，将回退到编辑器：{exc}", flush=True)

    editor = page.locator(
        ".zone-container[contenteditable='true'], "
        "div[class*='editor-comp-publish'][contenteditable='true']"
    ).first
    if not await editor.count():
        editor = page.locator("[contenteditable='true']").first
    await editor.wait_for(state="visible", timeout=120_000)
    await editor.click()
    await page.keyboard.press("Meta+A")
    await page.keyboard.press("Backspace")
    if not title_filled:
        # 旧 UI 兼容：标题+简介都写在编辑器内
        await page.keyboard.type(title)
        await page.keyboard.press("Enter")
        await page.keyboard.press("Enter")
    if desc and desc != title:
        await page.keyboard.type(desc[:500])
    # 抖音话题最多 5 个，超出后面的会糊成乱码；逐个输入并停顿，等联想框把 # 识别成话题。
    for tag in tags[:5]:
        await page.keyboard.type(f" #{tag.lstrip('#')}")
        await page.wait_for_timeout(600)
        await page.keyboard.press("Space")
        await page.wait_for_timeout(300)
    print(f"  已填写标题/简介/话题（{min(len(tags), 5)} 个）", flush=True)


async def _wait_upload_done(page, timeout_s: int = 300) -> None:
    for i in range(timeout_s // 2):
        if await page.locator('[class^="long-card"] div:has-text("重新上传")').count():
            print("  视频上传完成", flush=True)
            return
        if await page.locator('div.progress-div > div:has-text("上传失败")').count():
            raise DouyinPublishError("视频上传失败")
        if i and i % 10 == 0:
            print(f"  上传中… ({i * 2}s)", flush=True)
        await asyncio.sleep(2)
    raise DouyinPublishError("视频上传超时")


async def _upload_custom_cover(page, cover_path: Path) -> bool:
    if not cover_path.is_file():
        return False
    for text in ("选择封面", "编辑封面", "设置封面"):
        entry = page.get_by_text(text, exact=True).first
        try:
            if await entry.count() and await entry.is_visible():
                await entry.click(timeout=5000)
                await asyncio.sleep(1)
                break
        except Exception:
            continue

    for text in ("上传封面", "本地上传", "上传图片", "从本地上传"):
        btn = page.get_by_text(text, exact=False).first
        try:
            if await btn.count() and await btn.is_visible():
                await btn.click(timeout=5000)
                await asyncio.sleep(0.5)
                break
        except Exception:
            continue

    file_inputs = page.locator('input[type="file"]')
    count = await file_inputs.count()
    for i in range(count):
        inp = file_inputs.nth(i)
        try:
            accept = (await inp.get_attribute("accept")) or ""
            if accept and "image" not in accept:
                continue
            await inp.set_input_files(str(cover_path))
            await asyncio.sleep(1)
            for text in ("确定", "完成", "确认", "保存"):
                ok = page.get_by_role("button", name=text, exact=True).first
                if await ok.count() and await ok.is_visible():
                    try:
                        await ok.click(timeout=5000)
                        await asyncio.sleep(1)
                        print(f"  已上传自定义封面: {cover_path}", flush=True)
                        return True
                    except Exception:
                        pass
            print(f"  已上传自定义封面: {cover_path}", flush=True)
            return True
        except Exception:
            continue
    return False


async def _pick_cover(page, cover_path: Path | None = None) -> None:
    await _dismiss_overlays(page)
    hint = page.get_by_text("请设置封面后再发布").first
    if await hint.count() and await hint.is_visible():
        print("  需要设置封面", flush=True)

    if not cover_path:
        print("  跳过封面设置，使用抖音默认首帧封面", flush=True)
        return

    try:
        if await _upload_custom_cover(page, cover_path):
            return
    except Exception as exc:  # noqa: BLE001
        print(f"  自定义封面上传失败，保留默认首帧封面: {exc}", flush=True)

    choose = page.get_by_text("选择封面", exact=True).first
    if await choose.count() and await choose.is_visible():
        print("  提示: 请在浏览器中手动选封面", flush=True)


async def _click_radio_by_text(page, text: str) -> bool:
    for sel in (
        f'label.semi-radio:has-text("{text}")',
        f'label:has-text("{text}")',
    ):
        loc = page.locator(sel).first
        if not await loc.count():
            continue
        try:
            await loc.click(timeout=5000)
            return True
        except Exception:
            continue
    opt = page.get_by_text(text, exact=False).first
    if await opt.count():
        try:
            await opt.click(timeout=5000, force=True)
            return True
        except Exception:
            pass
    return False


def _declare_ai_enabled() -> bool:
    """默认不主动申报 AI 创作（申报反而影响流量，且内容含大量人工编排）。
    需要时设 DOUYIN_DECLARE_AI=1 打开。"""
    return _env("DOUYIN_DECLARE_AI", "").lower() in ("1", "true", "yes", "on")


async def _set_ai_declaration(page) -> None:
    if not _declare_ai_enabled():
        print("  跳过 AI 内容声明（DOUYIN_DECLARE_AI 未开启）", flush=True)
        return
    await _dismiss_overlays(page)
    for entry in ("自主声明", "发文助手自主声明", "高级设置"):
        el = page.get_by_text(entry, exact=False).first
        if not await el.count():
            continue
        try:
            if await el.is_visible():
                await el.click()
                await asyncio.sleep(0.5)
        except Exception:
            continue
    for label in ("内容由AI生成", "AI生成"):
        if await _click_radio_by_text(page, label):
            print("  已勾选 AI 内容声明", flush=True)
            return


async def _handle_declaration_modal(page) -> bool:
    modal = page.locator(".semi-modal-wrap").filter(has_text="声明").first
    if not await modal.count():
        modal = page.locator(".semi-modal-wrap").filter(has_text="AI").first
    if not await modal.count():
        return False
    try:
        if not await modal.is_visible():
            return False
    except Exception:
        return False
    if _declare_ai_enabled():
        for label in ("内容由AI生成", "AI生成"):
            radio = modal.locator(f'label.semi-radio:has-text("{label}")').first
            if await radio.count():
                try:
                    await radio.click(timeout=5000)
                    break
                except Exception:
                    continue
    # 不申报时：不勾任何 AI 选项，直接确认/继续发布把弹窗带过去
    for name in ("确定", "确认", "继续发布"):
        btn = modal.get_by_role("button", name=name, exact=True).first
        if await btn.count() and await btn.is_visible():
            try:
                await btn.click(timeout=5000)
                return True
            except Exception:
                continue
    return False


async def _click_publish(page, *, assist: bool) -> bool:
    if assist:
        print("\n>>> 半自动模式：请在 Chrome 中检查封面/声明，手动点击「发布」", flush=True)
        print(">>> 等待最多 10 分钟…", flush=True)
        for i in range(600):
            if "content/manage" in page.url:
                return True
            await _dismiss_overlays(page)
            await _handle_declaration_modal(page)
            if i and i % 30 == 0:
                print(f"  等待手动发布… ({i}s)", flush=True)
            await asyncio.sleep(1)
        return False

    for i in range(120):
        if "content/manage" in page.url:
            return True
        await _dismiss_overlays(page)
        await _handle_declaration_modal(page)
        await _pick_cover(page)
        btn = page.get_by_role("button", name="发布", exact=True).first
        if await btn.count():
            try:
                await btn.click()
                await asyncio.sleep(1)
                await _handle_declaration_modal(page)
            except Exception:
                pass
        if i and i % 10 == 0:
            print(f"  尝试发布… ({i * 2}s)", flush=True)
        await asyncio.sleep(2)
    return False


async def publish_video(
    video_path: Path,
    *,
    title: str,
    desc: str,
    tags: str = "",
    root: Path | None = None,
    headed: bool | None = None,
    assist: bool = False,
    cover_path: Path | None = None,
) -> None:
    _ensure_patchright()
    from patchright.async_api import async_playwright

    video_path = video_path.resolve()
    if not video_path.is_file():
        raise DouyinPublishError(f"视频不存在: {video_path}")

    if headed is None:
        headed = _env("SAU_HEADLESS", "").lower() not in ("1", "true", "yes")
        if _env("SAU_HEADED", "").lower() in ("1", "true", "yes"):
            headed = True
        elif headed is False and sys.platform == "darwin":
            headed = True  # macOS 默认有头

    if assist:
        headed = True

    tag_list = [t.strip().lstrip("#") for t in tags.split(",") if t.strip()][:5]
    cookie = cookie_path(root)

    launch = {
        "headless": not headed,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--lang=zh-CN",
            "--no-first-run",
        ],
    }
    chrome = _chrome_path()
    if chrome:
        launch["executable_path"] = chrome
    else:
        launch["channel"] = "chrome"
    if headed:
        launch["args"].append("--window-size=1440,900")

    print(f"发布模式: {'半自动有头' if assist else ('有头' if headed else '无头')}", flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch)
        context = await browser.new_context(
            storage_state=str(cookie),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            viewport={"width": 1440, "height": 900},
        )
        try:
            home = str(sau_home(root))
            if home not in sys.path:
                sys.path.insert(0, home)
            from utils.base_social_media import set_init_script

            context = await set_init_script(context)
        except Exception:
            pass
        page = await context.new_page()
        try:
            print("  打开上传页…", flush=True)
            await _goto(page, "https://creator.douyin.com/creator-micro/content/upload")
            await _require_logged_in(page)
            upload_input = await _wait_file_input(page, root=root)
            await upload_input.set_input_files(str(video_path))
            print("  已选择视频文件", flush=True)

            await _wait_publish_form(page)
            await _dismiss_overlays(page)
            await asyncio.sleep(1)

            await _fill_form(page, title, desc, tag_list)
            await _wait_upload_done(page)
            await _pick_cover(page, cover_path=cover_path)
            await _set_ai_declaration(page)

            ok = await _click_publish(page, assist=assist)
            if not ok:
                shot = (root or ROOT) / "logs" / "douyin_publish_fail.png"
                shot.parent.mkdir(parents=True, exist_ok=True)
                try:
                    await page.screenshot(path=str(shot), full_page=True, timeout=60_000)
                    print(f"  截图: {shot}", flush=True)
                except Exception:
                    pass
                raise DouyinPublishError(
                    "发布未完成。"
                    + ("请查看浏览器手动点击发布。" if assist else "可试: ./scripts/publish-douyin.sh --assist")
                )

            await context.storage_state(path=str(cookie))
            print("  发布成功，cookie 已更新", flush=True)
        finally:
            if assist and headed:
                await asyncio.sleep(2)
            await context.close()
            await browser.close()
