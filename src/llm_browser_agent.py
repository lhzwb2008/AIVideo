"""浏览器发布：确定性步骤优先，LLM（Opus 4.8）仅在失败时少量兜底。"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llm_vision_client import browser_max_steps, parse_json_response, vision_chat
from paths import ROOT


class LLMBrowserError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = _env(name, str(default))
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass
class AgentConfig:
    max_steps: int = 6
    action_delay_min: float = 1.0
    action_delay_max: float = 3.5
    typing_delay_min_ms: int = 50
    typing_delay_max_ms: int = 130
    screenshot_dir: Path = field(default_factory=lambda: ROOT / "logs" / "llm_browser")
    save_screenshots: bool = False
    use_deterministic: bool = True


@dataclass
class PageState:
    url: str
    title: str
    elements: list[dict[str, Any]]
    body_snippet: str
    screenshot: Path | None = None


async def human_pause(cfg: AgentConfig) -> None:
    await asyncio.sleep(random.uniform(cfg.action_delay_min, cfg.action_delay_max))


async def dismiss_overlays(page, *, platform_key: str = "") -> None:
    if platform_key == "shipinhao":
        for text in ("取消", "跳过", "暂不设置", "使用默认", "关闭", "确定"):
            loc = page.get_by_text(text, exact=False)
            count = await loc.count()
            for i in range(min(count, 2)):
                item = loc.nth(i)
                try:
                    if await item.is_visible():
                        await item.click(timeout=2000, force=True)
                        await asyncio.sleep(0.5)
                except Exception:
                    continue
        for sel in (
            'div.weui-desktop-dialog:has-text("封面") button:has-text("取消")',
            'div.weui-desktop-dialog:has-text("封面") button:has-text("关闭")',
            'div.weui-desktop-dialog button:has-text("取消")',
        ):
            btn = page.locator(sel).first
            try:
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=2000, force=True)
                    await asyncio.sleep(0.5)
            except Exception:
                continue
    if platform_key == "xiaohongshu":
        for name in ("Block", "阻止", "不允许"):
            btn = page.get_by_role("button", name=name).first
            try:
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=2000, force=True)
                    await asyncio.sleep(0.5)
            except Exception:
                pass
        for sel in (
            'div.d-modal.cover-modal button:has-text("取消")',
            'div.cover-modal button:has-text("取消")',
            'div.d-modal.cover-modal button:has-text("关闭")',
            'div.cover-modal button:has-text("关闭")',
        ):
            btn = page.locator(sel).first
            try:
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=3000, force=True)
                    await asyncio.sleep(0.5)
            except Exception:
                continue
    if platform_key == "douyin":
        for text in ("取消", "跳过", "暂不设置", "使用默认", "关闭"):
            loc = page.get_by_text(text, exact=False)
            count = await loc.count()
            for i in range(min(count, 2)):
                item = loc.nth(i)
                try:
                    if await item.is_visible():
                        await item.click(timeout=2000, force=True)
                        await asyncio.sleep(0.5)
                except Exception:
                    continue
        for sel in (
            'div[id*="creator-content-modal"] button:has-text("取消")',
            'div[class*="cover"] button:has-text("取消")',
            'div[class*="cover"] button:has-text("关闭")',
        ):
            btn = page.locator(sel).first
            try:
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=2000, force=True)
                    await asyncio.sleep(0.5)
            except Exception:
                continue
    for text in ("我知道了", "知道了", "关闭", "跳过", "暂不", "以后再说"):
        loc = page.get_by_text(text, exact=False)
        count = await loc.count()
        for i in range(min(count, 3)):
            item = loc.nth(i)
            try:
                if await item.is_visible():
                    await item.click(timeout=2000, force=True)
                    await asyncio.sleep(0.5)
            except Exception:
                continue
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass


async def human_type(page, text: str, cfg: AgentConfig) -> None:
    for ch in text:
        await page.keyboard.type(ch)
        await asyncio.sleep(
            random.randint(cfg.typing_delay_min_ms, cfg.typing_delay_max_ms) / 1000.0
        )


async def extract_page_state(page, *, screenshot_path: Path | None) -> PageState:
    if screenshot_path:
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await page.evaluate(
                """() => Promise.race([
                  document.fonts ? document.fonts.ready : Promise.resolve(),
                  new Promise((r) => setTimeout(r, 2500)),
                ])"""
            )
            await page.screenshot(
                path=str(screenshot_path),
                full_page=False,
                timeout=20_000,
                animations="disabled",
            )
        except Exception as exc:
            print(f"  ⚠️ 截图失败，继续 DOM-only: {exc}", flush=True)
            screenshot_path = None

    payload = await page.evaluate(
        """() => {
          const items = [];
          const nodes = document.querySelectorAll(
            'button, a, input, textarea, select, [contenteditable="true"], [role="button"], label, [class*="upload"]'
          );
          nodes.forEach((el, idx) => {
            const rect = el.getBoundingClientRect();
            if (rect.width < 2 || rect.height < 2) return;
            const style = window.getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') return;
            const text = (
              el.innerText ||
              el.getAttribute('placeholder') ||
              el.getAttribute('aria-label') ||
              el.getAttribute('title') ||
              ''
            ).replace(/\\s+/g, ' ').trim().slice(0, 100);
            items.push({
              ref: idx,
              tag: el.tagName.toLowerCase(),
              role: el.getAttribute('role') || '',
              type: el.getAttribute('type') || '',
              text,
              accept: el.getAttribute('accept') || '',
              editable: el.isContentEditable || el.tagName === 'TEXTAREA' || el.tagName === 'INPUT',
            });
          });
          const body = (document.body && document.body.innerText || '').replace(/\\s+/g, ' ').trim();
          return {
            url: location.href,
            title: document.title || '',
            elements: items.slice(0, 100),
            body_snippet: body.slice(0, 1200),
          };
        }"""
    )
    return PageState(
        url=str(payload.get("url") or page.url),
        title=str(payload.get("title") or ""),
        elements=list(payload.get("elements") or []),
        body_snippet=str(payload.get("body_snippet") or ""),
        screenshot=screenshot_path,
    )


async def resolve_element(page, ref: int):
    return page.locator(
        'button, a, input, textarea, select, [contenteditable="true"], [role="button"], label, [class*="upload"]'
    ).nth(ref)


async def try_upload_video(
    page, video_path: Path, *, platform: str = "douyin", root: Path | None = None
) -> bool:
    """按平台优先注入 file input；抖音复用 douyin_publisher 等待逻辑。"""
    platform = (platform or "douyin").lower()

    if platform == "douyin":
        try:
            from douyin_publisher import (
                _dismiss_overlays,
                _goto,
                _require_logged_in,
                _wait_file_input,
            )

            upload_url = "https://creator.douyin.com/creator-micro/content/upload"
            if "content/upload" not in (page.url or ""):
                await _goto(page, upload_url)
                await asyncio.sleep(2)
            for attempt in range(2):
                try:
                    await _require_logged_in(page)
                    break
                except Exception as exc:
                    if attempt == 0:
                        print(f"  [upload] 上传页登录态待确认，刷新重试… ({exc})", flush=True)
                        await _goto(page, upload_url)
                        await asyncio.sleep(3)
                    else:
                        raise
            await _dismiss_overlays(page)
            upload_input = await _wait_file_input(page, timeout_s=120, root=root or ROOT)
            await upload_input.set_input_files(str(video_path))
            print(f"  [upload] 已选择视频: {video_path.name}", flush=True)
            return True
        except Exception as exc:
            print(f"  [upload] 抖音专用上传失败，尝试通用方式: {exc}", flush=True)

    if platform == "shipinhao":
        try:
            await page.wait_for_url("**/platform/post/create**", timeout=60_000)
            await asyncio.sleep(2)
            inp = page.locator('input[type="file"]').first
            await inp.wait_for(state="attached", timeout=60_000)
            await inp.set_input_files(str(video_path))
            print(f"  [upload] 视频号已选择视频: {video_path.name}", flush=True)
            await asyncio.sleep(2)
            return True
        except Exception as exc:
            print(f"  [upload] 视频号专用上传失败: {exc}", flush=True)

    if platform == "xiaohongshu":
        try:
            loc = page.locator(
                "div[class^='upload-content'] input.upload-input, input.upload-input"
            ).first
            await loc.wait_for(state="attached", timeout=60_000)
            await loc.set_input_files(str(video_path))
            print(f"  [upload] 小红书已选择视频: {video_path.name}", flush=True)
            await asyncio.sleep(3)
            await dismiss_overlays(page, platform_key="xiaohongshu")
            return True
        except Exception as exc:
            print(f"  [upload] 小红书专用上传失败: {exc}", flush=True)

    platform_selectors = {
        "xiaohongshu": (
            "div[class^='upload-content'] input.upload-input",
            "input.upload-input",
            'div.progress-div [class^="upload-btn-input"]',
        ),
        "shipinhao": (
            "input[type='file'][accept*='video']",
            "input[type='file']",
        ),
    }
    selectors = platform_selectors.get(platform, ()) + (
        "input[type='file'][accept*='video']",
        "input.semi-upload-hidden-input",
        "input[type='file']",
    )
    for sel in selectors:
        loc = page.locator(sel).first
        if not await loc.count():
            continue
        try:
            await loc.set_input_files(str(video_path))
            print(f"  [upload] 已通过 file input 选择视频: {video_path.name}", flush=True)
            return True
        except Exception:
            continue

    trigger_texts = {
        "xiaohongshu": ("上传视频", "点击上传", "上传"),
        "shipinhao": ("上传", "点击上传", "从相册选择"),
        "douyin": ("点击上传", "上传视频"),
    }
    for text in trigger_texts.get(platform, ("点击上传", "上传")):
        trigger = page.get_by_text(text, exact=False).first
        if not await trigger.count():
            continue
        try:
            async with page.expect_file_chooser(timeout=8000) as fc_info:
                await trigger.click(timeout=5000)
            chooser = await fc_info.value
            await chooser.set_files(str(video_path))
            print(f"  [upload] 已通过 file chooser 选择视频 ({text})", flush=True)
            return True
        except Exception:
            continue
    return False


def _build_system_prompt(platform: str) -> str:
    return f"""你是 {platform} 发布助手。根据页面元素列表（和可选截图）输出**一步** JSON。
字段：thought, action, ref, text, wait_seconds, reason
action：click | type | wait | press_key | done | need_human
优先 wait 等上传完成；不要重复同一 click；卡住时用 need_human。
只输出 JSON，不要 markdown。"""


def _build_user_prompt(
    *,
    task: str,
    state: PageState,
    step: int,
    history: list[str],
    video_path: Path | None,
) -> str:
    history_text = "\n".join(f"- {h}" for h in history[-8:]) or "（无）"
    elements_json = json.dumps(state.elements[:45], ensure_ascii=False, indent=2)
    return f"""## 任务
{task}

## 本地视频
{video_path or '（已上传或无）'}

## 当前步骤
第 {step} 步

## 最近操作
{history_text}

## 页面
URL: {state.url}
Title: {state.title}
正文摘要: {state.body_snippet[:800]}

## 可交互元素（ref 用于 click/type/upload）
{elements_json}

请输出 JSON。"""


def _check_success(
    state: PageState,
    success_patterns: list[str],
    *,
    start_url: str = "",
    platform_key: str = "",
) -> bool:
    url = state.url.lower()
    if any(x in url for x in ("login", "passport", "registermidpage", "account/login")):
        return False

    if platform_key == "xiaohongshu":
        if "__debugger__" in url or "bind_status=not_bind" in url:
            return False
        if "publish/success" in url:
            if any(
                t in state.body_snippet
                for t in ("发布成功", "发布完成", "笔记发布成功", "已提交")
            ):
                return True
            return False
        # 已离开发布编辑页（常见于提交后异步跳转）
        if "creator.xiaohongshu.com" in url and "publish/publish" not in url:
            if any(
                seg in url
                for seg in ("note-manage", "content-manager", "creator/home")
            ):
                return True

    url_success_markers = (
        "content/manage",
        "platform/post/list",
        "note-manage",
        "content-manager",
    )
    for pat in url_success_markers:
        if pat in url:
            return True

    if "publish/success" in url and platform_key != "xiaohongshu":
        return True

    start = (start_url or "").lower().split("?")[0]
    current = url.split("?")[0]
    still_uploading = any(
        seg in current
        for seg in ("content/upload", "post/create", "publish/publish")
    )
    if still_uploading:
        return False

    if start and current != start:
        for text in ("发布成功", "发表成功", "笔记发布成功", "提交成功", "已发表"):
            if text in state.body_snippet:
                return True

    for pat in success_patterns:
        if pat.lower() in url:
            if pat.lower() == "publish/success" and platform_key == "xiaohongshu":
                continue
            return True
    return False


def _parse_tags(raw: str) -> list[str]:
    return [t.strip().lstrip("#") for t in str(raw or "").split(",") if t.strip()][:5]


async def _wait_xhs_video_uploaded(page, *, timeout_s: int = 300) -> None:
    """视频传完、标题框出现即可填表，不等封面。"""
    for _ in range(timeout_s // 2):
        await dismiss_overlays(page, platform_key="xiaohongshu")
        title = page.locator('input[placeholder*="填写标题"]').first
        if await title.count():
            try:
                if await title.is_visible():
                    return
            except Exception:
                pass
        await asyncio.sleep(2)
    raise LLMBrowserError("小红书视频上传超时")


async def _xhs_fill_form(
    page, *, title: str, body: str, tags: list[str], cfg: AgentConfig
) -> None:
    tin = page.locator('input[placeholder*="填写标题"]').first
    await tin.wait_for(state="visible", timeout=120_000)
    await tin.fill(title[:20])
    desc_loc = page.locator(
        'p[data-placeholder*="输入正文描述"], div[data-placeholder*="输入正文描述"]'
    ).first
    if await desc_loc.count():
        await desc_loc.click(timeout=10_000)
        await page.keyboard.press("Meta+A")
        await page.keyboard.press("Backspace")
        await human_type(page, body[:1000], cfg)
    for tag in tags:
        await page.keyboard.type(f"#{tag}", delay=30)
        try:
            topic = page.locator("#creator-editor-topic-container").first
            await topic.wait_for(state="visible", timeout=3000)
            item = page.locator("#creator-editor-topic-container .item").first
            await item.click(timeout=2000)
        except Exception:
            await page.keyboard.press("Space")
        await asyncio.sleep(0.3)
    print(f"  [script] 已填写标题/描述（{len(body)} 字）", flush=True)


async def _xhs_form_ready(page) -> bool:
    """标题框已有内容 = 视频已上传且表单已填（勿再触发上传）。"""
    tin = page.locator('input[placeholder*="填写标题"]').first
    if not await tin.count():
        return False
    try:
        if not await tin.is_visible():
            return False
        val = (await tin.input_value()).strip()
        return len(val) > 0
    except Exception:
        return False


async def _xhs_switch_is_on(locator) -> bool:
    try:
        if await locator.is_checked():
            return True
    except Exception:
        pass
    try:
        aria = await locator.get_attribute("aria-checked")
        if aria == "true":
            return True
    except Exception:
        pass
    try:
        cls = (await locator.get_attribute("class")) or ""
        if any(x in cls for x in ("checked", "active", "is-checked", "is-open")):
            return True
    except Exception:
        pass
    return False


async def _xhs_disable_pk_cover(page) -> None:
    """关闭 PK 封面。误开会导致「请至少添加一张 PK 封面」无法发布。"""
    pk_error = await page.get_by_text("请至少添加一张 PK 封面", exact=False).count()

    toggled = await page.evaluate(
        """() => {
          const isOn = (sw) => {
            if (sw.checked === true) return true;
            if (sw.getAttribute('aria-checked') === 'true') return true;
            const cls = sw.className || '';
            return /checked|active|is-checked|is-open/i.test(cls);
          };
          for (const el of document.querySelectorAll('*')) {
            const t = (el.textContent || '').trim();
            if (t !== 'PK封面' && !t.startsWith('PK封面')) continue;
            let row = el;
            for (let i = 0; i < 6 && row; i++, row = row.parentElement) {
              const sw = row.querySelector(
                '[role="switch"], [class*="switch" i], input[type="checkbox"]'
              );
              if (sw && isOn(sw)) { sw.click(); return true; }
            }
          }
          return false;
        }"""
    )
    if toggled:
        print("  [script] 已关闭 PK 封面", flush=True)
        await asyncio.sleep(0.6)
        return

    if pk_error:
        for label_text in ("PK封面", "PK 封面"):
            labels = page.get_by_text(label_text, exact=False)
            count = await labels.count()
            for i in range(min(count, 5)):
                label = labels.nth(i)
                try:
                    if not await label.is_visible():
                        continue
                    row = label.locator("xpath=ancestor::div[1]")
                    sw = row.locator(
                        '[role="switch"], [class*="switch"], input[type="checkbox"]'
                    ).first
                    if await sw.count():
                        await sw.click(timeout=3000, force=True)
                        print("  [script] 已关闭 PK 封面（错误提示触发）", flush=True)
                        await asyncio.sleep(0.6)
                        return
                except Exception:
                    continue


async def _xhs_in_cover_section(locator) -> bool:
    """元素是否在封面编辑区内（勿在此区域点开关）。"""
    try:
        return await locator.locator(
            'xpath=ancestor::div[contains(@class, "cover-plugin") or '
            'contains(@class, "cover-modal") or contains(@class, "cover-container")][1]'
        ).count() > 0
    except Exception:
        return False


async def _xhs_confirm_original_dialog(page) -> None:
    """原创声明弹窗：勾选条款并确认。"""
    for label in (
        "我已阅读并同意",
        "阅读并同意",
        "同意《",
        "原创声明",
    ):
        cb = page.get_by_label(label, exact=False).first
        if not await cb.count():
            continue
        try:
            if not await cb.is_checked():
                await cb.check(timeout=5000)
        except Exception:
            try:
                await cb.click(timeout=5000, force=True)
            except Exception:
                pass
    for name in ("声明原创", "确认", "确定", "同意"):
        btn = page.get_by_role("button", name=name).first
        if not await btn.count():
            continue
        try:
            if await btn.is_visible():
                await btn.click(timeout=5000)
                await asyncio.sleep(0.8)
                return
        except Exception:
            continue


async def _xhs_toggle_original_switch(page, *, settings_opened: bool = False) -> bool:
    """仅在「设置」面板内打开「声明原创」，避免误触封面区的 PK 开关。"""
    roots = []
    if settings_opened:
        for sel in (
            '[class*="publish-setting"]',
            '[class*="setting-drawer"]',
            '[class*="Setting"]',
            ".publish-container",
        ):
            loc = page.locator(sel).first
            if await loc.count():
                roots.append(loc)
    if not roots:
        roots = [page]

    for root in roots:
        for text in ("声明原创", "原创声明"):
            labels = root.get_by_text(text, exact=True)
            count = await labels.count()
            for i in range(count):
                label = labels.nth(i)
                try:
                    if not await label.is_visible():
                        continue
                    if await _xhs_in_cover_section(label):
                        continue
                    row = label.locator(
                        "xpath=ancestor::div[contains(@class,'item') or "
                        "contains(@class,'row') or contains(@class,'cell') or "
                        "contains(@class,'setting')][1]"
                    )
                    switch = row.locator(
                        '[role="switch"], [class*="switch"], input[type="checkbox"]'
                    ).first
                    if not await switch.count():
                        continue
                    if not await _xhs_switch_is_on(switch):
                        await switch.click(timeout=5000, force=True)
                    await asyncio.sleep(0.5)
                    await _xhs_confirm_original_dialog(page)
                    return True
                except Exception:
                    continue
    return False


async def _xhs_open_settings(page) -> bool:
    """打开发布页左下角「设置」面板。"""
    try:
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(0.4)
    except Exception:
        pass
    for loc in (
        page.locator('[class*="publish"] >> text=设置').last,
        page.locator('[class*="footer"] >> text=设置').last,
        page.locator(".publish-container").get_by_text("设置", exact=True).last,
        page.get_by_role("button", name="设置").last,
        page.get_by_text("设置", exact=True).last,
    ):
        if not await loc.count():
            continue
        try:
            await loc.scroll_into_view_if_needed(timeout=5000)
            if await loc.is_visible():
                await loc.click(timeout=5000, force=True)
                await asyncio.sleep(0.8)
                return True
        except Exception:
            continue
    return False


async def _xhs_declare_original(page) -> bool:
    """勾选小红书原创声明（优先设置面板，也尝试页内开关）。"""
    await _xhs_disable_pk_cover(page)

    if await _xhs_toggle_original_switch(page, settings_opened=False):
        print("  [script] 已勾选原创声明", flush=True)
        await _xhs_disable_pk_cover(page)
        return True

    if await _xhs_open_settings(page):
        if await _xhs_toggle_original_switch(page, settings_opened=True):
            print("  [script] 已勾选原创声明（设置面板）", flush=True)
            await _xhs_disable_pk_cover(page)
            return True

    print("  [script] ⚠️ 未找到原创声明开关，继续尝试发布…", flush=True)
    await _xhs_disable_pk_cover(page)
    return False


async def _xhs_publish_succeeded(page) -> bool:
    url = (page.url or "").lower()
    if "note-manage" in url or "content-manager" in url:
        return True
    if "publish/success" in url:
        if "__debugger__" in url or "bind_status=not_bind" in url:
            return False
        return True
    if "creator.xiaohongshu.com" in url and "publish/publish" not in url:
        if any(seg in url for seg in ("note-manage", "content-manager", "creator/home")):
            return True
    try:
        body = await page.evaluate(
            "() => (document.body && document.body.innerText) || ''"
        )
    except Exception:
        body = ""
    for ok in ("发布成功", "发布完成", "笔记发布成功", "已发布", "已提交", "提交成功"):
        if ok in body:
            return True
    for ok in ("发布成功", "发布完成", "笔记发布成功", "已发布", "已提交"):
        loc = page.get_by_text(ok, exact=False).first
        try:
            if await loc.count() and await loc.is_visible():
                return True
        except Exception:
            pass
    return False


async def _xhs_click_publish(page, *, start_url: str = "") -> bool:
    confirm_texts = ("确认发布", "确定发布", "确认", "确定")
    clicked = False

    for _ in range(60):
        await dismiss_overlays(page, platform_key="xiaohongshu")
        await _xhs_disable_pk_cover(page)
        if await page.get_by_text("请至少添加一张 PK 封面", exact=False).count():
            await _xhs_disable_pk_cover(page)
            await asyncio.sleep(0.5)

        if await _xhs_publish_succeeded(page):
            return True

        for name in ("立即发布", "发布"):
            for btn in (
                page.get_by_role("button", name=name, exact=True),
                page.locator(f'button:text-is("{name}")'),
                page.locator(".publish-container").locator(f'button:has-text("{name}")').last,
            ):
                try:
                    if not await btn.count():
                        continue
                    target = btn.first if await btn.count() == 1 else btn.last
                    await target.scroll_into_view_if_needed(timeout=3000)
                    try:
                        if not await target.is_enabled():
                            continue
                    except Exception:
                        pass
                    await target.click(timeout=6000)
                    clicked = True
                    break
                except Exception:
                    continue
            if clicked:
                break

        await asyncio.sleep(0.8)
        for ct in confirm_texts:
            btn = page.get_by_role("button", name=ct).first
            try:
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=3000)
                    clicked = True
            except Exception:
                pass
        await asyncio.sleep(1)

        if await _xhs_publish_succeeded(page):
            return True

    if clicked:
        for _ in range(45):
            await asyncio.sleep(2)
            if await _xhs_publish_succeeded(page):
                return True
        # 已点击发布但页面未跳转：后台可能已提交（用户反馈常见）
        print("  [script] 已点击发布，等待后台确认…", flush=True)
        return True
    return False


async def _shipinhao_declare_original(page) -> None:
    """勾选原创并完成声明弹窗（复用 SAU tencent_uploader 逻辑）。"""
    for label in ("视频为原创", "声明原创"):
        cb = page.get_by_label(label).first
        if await cb.count():
            try:
                if not await cb.is_checked():
                    await cb.check(timeout=5000)
            except Exception:
                try:
                    await cb.click(timeout=5000, force=True)
                except Exception:
                    pass
    try:
        terms_visible = await page.locator(
            'label:has-text("我已阅读并同意 《视频号原创声明使用条款》")'
        ).is_visible()
    except Exception:
        terms_visible = False
    if terms_visible:
        await page.get_by_label("我已阅读并同意 《视频号原创声明使用条款》").check()
        btn = page.get_by_role("button", name="声明原创")
        if await btn.count():
            await btn.click(timeout=5000)
            await asyncio.sleep(1)
    declare = page.locator('div.label span:has-text("声明原创")')
    if await declare.count():
        checkbox = page.locator(
            "div.declare-original-checkbox input.ant-checkbox-input"
        )
        if await checkbox.count() and not await checkbox.is_disabled():
            await checkbox.click()
        dialog_btn = page.locator('button:has-text("声明原创"):visible')
        if await dialog_btn.count():
            await dialog_btn.click(timeout=5000)
            await asyncio.sleep(1)


async def _shipinhao_fill_form(page, body: str, cfg: AgentConfig) -> None:
    text = body.strip()
    editor = page.locator("div.input-editor").first
    await editor.wait_for(state="visible", timeout=120_000)
    await editor.scroll_into_view_if_needed(timeout=15_000)
    await editor.click(timeout=10_000)
    await page.keyboard.press("Meta+A")
    await page.keyboard.press("Backspace")
    await human_type(page, text[:500], cfg)
    print(f"  [script] 已填写描述（{len(text)} 字）", flush=True)


async def _wait_shipinhao_video_uploaded(page, *, timeout_s: int = 300) -> None:
    """视频传完即可填表，不等封面预览。"""
    for _ in range(timeout_s // 2):
        await dismiss_overlays(page, platform_key="shipinhao")
        editor = page.locator("div.input-editor").first
        uploaded = page.locator(
            'div.tag-inner:has-text("删除"), div.media-status-content:has-text("删除")'
        ).first
        if await uploaded.count() and await editor.count():
            try:
                if await editor.is_visible():
                    return
            except Exception:
                pass
        await asyncio.sleep(2)
    raise LLMBrowserError("视频号视频上传超时")


async def _shipinhao_click_publish(page) -> bool:
    pub = page.locator('div.form-btns button:has-text("发表")').first
    await pub.wait_for(state="visible", timeout=30_000)
    await pub.scroll_into_view_if_needed(timeout=10_000)
    for _ in range(30):
        cls = await pub.get_attribute("class") or ""
        if "disabled" not in cls and "weui-desktop-btn_disabled" not in cls:
            break
        await asyncio.sleep(2)
    else:
        return False

    await pub.click(timeout=10_000, force=True)
    for _ in range(30):
        url = (page.url or "").lower()
        if "platform/post/list" in url:
            return True
        try:
            body = await page.evaluate(
                "() => (document.body && document.body.innerText) || ''"
            )
        except Exception:
            body = ""
        if any(t in body for t in ("发表成功", "已发表", "提交成功")):
            return True
        await asyncio.sleep(1)
    return False


async def _wait_video_ready(page, platform_key: str, *, timeout_s: int = 300) -> None:
    if platform_key == "douyin":
        from douyin_publisher import _wait_publish_form, _wait_upload_done

        await _wait_publish_form(page)
        await _wait_upload_done(page)
        return

    if platform_key == "xiaohongshu":
        title = page.locator('input[placeholder*="填写标题"]').first
        await title.wait_for(state="visible", timeout=timeout_s * 1000)
        return

    if platform_key == "shipinhao":
        await _wait_shipinhao_video_uploaded(page)
        return


async def try_deterministic_publish(
    page,
    *,
    platform_key: str,
    fields: dict[str, Any],
    cfg: AgentConfig,
) -> bool:
    """固定脚本填表+发布，成功则零 LLM 调用。"""
    await dismiss_overlays(page, platform_key=platform_key)
    title = str(fields.get("title") or "")[:30]
    desc = str(fields.get("desc") or "")
    tags = _parse_tags(str(fields.get("tags") or ""))
    tag_line = " ".join(f"#{t}" for t in tags)

    if platform_key == "douyin":
        from douyin_publisher import _click_publish, _dismiss_overlays, _fill_form

        await _wait_video_ready(page, platform_key)
        await _dismiss_overlays(page)
        await _fill_form(page, title, desc, tags)
        return await _click_publish(page, assist=False)

    if platform_key == "xiaohongshu":
        start_url = page.url
        await _wait_xhs_video_uploaded(page)
        await dismiss_overlays(page, platform_key="xiaohongshu")
        await _xhs_disable_pk_cover(page)
        body = desc if tag_line in desc else f"{desc}\n\n{tag_line}".strip()
        await _xhs_fill_form(page, title=title[:20], body=body, tags=tags, cfg=cfg)
        await _xhs_disable_pk_cover(page)
        if not await _xhs_declare_original(page):
            print("  [script] 原创声明可能未勾选，继续尝试发布…", flush=True)
        await dismiss_overlays(page, platform_key="xiaohongshu")
        if await _xhs_click_publish(page, start_url=start_url):
            print("  [script] 已点击发布", flush=True)
            return True
        return False

    if platform_key == "shipinhao":
        await _wait_shipinhao_video_uploaded(page)
        await dismiss_overlays(page, platform_key="shipinhao")
        await _shipinhao_fill_form(page, f"{desc} {tag_line}".strip(), cfg)
        await _shipinhao_declare_original(page)
        print("  [script] 已勾选原创声明", flush=True)
        await dismiss_overlays(page, platform_key="shipinhao")
        if await _shipinhao_click_publish(page):
            print("  [script] 已点击发表", flush=True)
            return True
        return False

    return False


def _action_signature(action: dict[str, Any]) -> str:
    return "|".join(
        [
            str(action.get("action") or ""),
            str(action.get("ref") or ""),
            str(action.get("text") or "")[:24],
        ]
    )


def _check_stuck_loop(history: list[str], signature: str) -> None:
    limit = _env_int("LLM_BROWSER_REPEAT_LIMIT", 2)
    if history.count(signature) >= limit:
        raise LLMBrowserError(
            f"检测到重复操作 {signature!r}，已停止（避免频繁交互触发风控）"
        )


async def execute_action(
    page,
    action: dict[str, Any],
    *,
    video_path: Path | None,
    platform_key: str,
    cfg: AgentConfig,
) -> str:
    name = str(action.get("action") or "").strip().lower()
    ref = action.get("ref")
    text = str(action.get("text") or "")
    scroll_y = action.get("scroll_y")
    wait_seconds = action.get("wait_seconds")

    if name == "wait":
        secs = float(wait_seconds or random.uniform(2, 5))
        await asyncio.sleep(min(max(secs, 0.5), 30))
        return f"wait {secs:.1f}s"

    if name == "scroll":
        delta = int(scroll_y or random.randint(200, 600))
        await page.mouse.wheel(0, delta)
        await human_pause(cfg)
        return f"scroll {delta}"

    if name == "press_key":
        key = text or "Enter"
        await page.keyboard.press(key)
        await human_pause(cfg)
        return f"press {key}"

    if name == "upload":
        if video_path and await try_upload_video(
            page, video_path, platform=platform_key, root=ROOT
        ):
            await human_pause(cfg)
            return f"upload {video_path.name}"
        if ref is not None:
            el = await resolve_element(page, int(ref))
            if video_path:
                try:
                    await el.set_input_files(str(video_path))
                    await human_pause(cfg)
                    return f"upload via ref {ref}"
                except Exception as exc:
                    raise LLMBrowserError(f"upload ref={ref} 失败: {exc}") from exc
        raise LLMBrowserError("upload 失败：找不到 file input")

    if name in ("click", "type"):
        if ref is None:
            raise LLMBrowserError(f"{name} 缺少 ref")
        await dismiss_overlays(page, platform_key=platform_key)
        el = await resolve_element(page, int(ref))
        await el.scroll_into_view_if_needed(timeout=10_000)
        await human_pause(cfg)
        await el.click(timeout=15_000, force=True)
        if name == "type":
            await page.keyboard.press("Meta+A")
            await page.keyboard.press("Backspace")
            await human_type(page, text, cfg)
        return f"{name} ref={ref} text={text[:40]!r}"

    raise LLMBrowserError(f"未知 action: {name}")


async def run_agent(
    page,
    *,
    platform: str,
    platform_key: str = "",
    task: str,
    fields: dict[str, Any] | None = None,
    video_path: Path | None = None,
    success_patterns: list[str] | None = None,
    cfg: AgentConfig | None = None,
    pre_upload: bool = True,
) -> dict[str, Any]:
    platform_key = platform_key or platform
    fields = fields or {}
    cfg = cfg or AgentConfig(
        max_steps=browser_max_steps(),
        action_delay_min=_env_float("LLM_BROWSER_DELAY_MIN", 1.0),
        action_delay_max=_env_float("LLM_BROWSER_DELAY_MAX", 3.5),
        save_screenshots=_env("LLM_BROWSER_SAVE_SCREENSHOTS", "0").lower()
        in ("1", "true", "yes", "on"),
        use_deterministic=_env("LLM_BROWSER_DETERMINISTIC", "1").lower()
        not in ("0", "false", "no", "off"),
    )
    success_patterns = success_patterns or ["manage", "发布成功", "已发布", "发表成功"]
    history: list[str] = []
    llm_calls = 0
    cfg.screenshot_dir.mkdir(parents=True, exist_ok=True)
    start_url = page.url
    llm_video_path = video_path

    if pre_upload and video_path:
        await human_pause(cfg)
        if not await try_upload_video(
            page, video_path, platform=platform_key, root=ROOT
        ):
            raise LLMBrowserError("视频上传失败，请检查 cookie 或登录态后重试（勿自动连跑）")

    state = await extract_page_state(page, screenshot_path=None)
    if _check_success(state, success_patterns, start_url=start_url, platform_key=platform_key):
        return {"ok": True, "steps": 0, "llm_calls": 0, "url": state.url, "history": []}

    if cfg.use_deterministic and fields:
        print("  [script] 尝试确定性填表+发布（零 LLM）…", flush=True)
        try:
            if await try_deterministic_publish(
                page, platform_key=platform_key, fields=fields, cfg=cfg
            ):
                for _ in range(45):
                    await asyncio.sleep(2)
                    state = await extract_page_state(page, screenshot_path=None)
                    if _check_success(
                        state, success_patterns, start_url=start_url, platform_key=platform_key
                    ):
                        print("  [script] 确定性发布完成", flush=True)
                        return {
                            "ok": True,
                            "steps": 0,
                            "llm_calls": 0,
                            "url": state.url,
                            "history": ["deterministic"],
                        }
                state = await extract_page_state(page, screenshot_path=None)
                print(
                    "  [script] 已提交发布（页面未跳转，请在笔记管理后台确认）",
                    flush=True,
                )
                return {
                    "ok": True,
                    "steps": 0,
                    "llm_calls": 0,
                    "url": state.url,
                    "history": ["deterministic", "submitted_unverified"],
                }
        except Exception as exc:
            print(f"  [script] 确定性步骤未完成: {exc}", flush=True)

    if platform_key == "xiaohongshu" and await _xhs_form_ready(page):
        llm_video_path = None
        print("  [script] 表单已就绪，LLM 兜底不再重复上传视频", flush=True)

    print(f"  [agent] 进入 LLM 兜底（最多 {cfg.max_steps} 步）…", flush=True)
    for step in range(1, cfg.max_steps + 1):
        await dismiss_overlays(page, platform_key=platform_key)
        use_shot = cfg.save_screenshots or step == 1
        shot = cfg.screenshot_dir / f"{platform_key}_llm_{step:02d}.png" if use_shot else None
        state = await extract_page_state(page, screenshot_path=shot)
        if _check_success(state, success_patterns, start_url=start_url, platform_key=platform_key):
            print(f"  [agent] 成功 step={step} url={state.url}", flush=True)
            return {
                "ok": True,
                "steps": step,
                "llm_calls": llm_calls,
                "url": state.url,
                "history": history,
            }

        user_prompt = _build_user_prompt(
            task=task,
            state=state,
            step=step,
            history=history,
            video_path=llm_video_path,
        )
        print(f"  [agent] LLM {step}/{cfg.max_steps}…", flush=True)
        llm_calls += 1
        raw = vision_chat(
            system=_build_system_prompt(platform),
            user_text=user_prompt,
            screenshot=state.screenshot if step == 1 else None,
            max_tokens=260,
        )
        action = parse_json_response(raw)
        thought = str(action.get("thought") or "")[:100]
        act = str(action.get("action") or "")
        print(f"  [agent] → {act} | {thought}", flush=True)

        if act == "done":
            return {
                "ok": True,
                "steps": step,
                "llm_calls": llm_calls,
                "url": state.url,
                "history": history,
            }
        if act == "need_human":
            raise LLMBrowserError(
                f"需要人工介入: {action.get('reason') or thought or '扫码/验证码'}"
            )

        sig = _action_signature(action)
        _check_stuck_loop(history, sig)
        summary = await execute_action(
            page, action, video_path=llm_video_path, platform_key=platform_key, cfg=cfg
        )
        history.append(summary)
        await human_pause(cfg)

    raise LLMBrowserError(
        f"LLM 兜底 {cfg.max_steps} 步仍未完成，已停止（请人工检查后台，勿立即重试）"
    )
