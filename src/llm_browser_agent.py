"""浏览器发布：LLM 视觉逐步操作（模拟真人）；简单平台可开确定性填表加速。"""

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


def _llm_first_enabled(env_key: str) -> bool:
    """默认 1：填表/发布交 LLM 逐步操作；设为 0 才走固定脚本。"""
    return _env(env_key, "1").lower() not in ("0", "false", "no", "off")


def platform_use_deterministic(platform_key: str) -> bool:
    """是否走固定脚本填表。"""
    # 小红书曾因固定脚本发布被封，强制 LLM 逐步操作（忽略全局/per-platform 确定性开关）
    if platform_key == "xiaohongshu":
        return False
    if _env("LLM_BROWSER_DETERMINISTIC", "1").lower() in ("0", "false", "no", "off"):
        return False
    per = _env(f"LLM_BROWSER_DETERMINISTIC_{platform_key.upper()}", "")
    if per:
        return per.lower() in ("1", "true", "yes", "on")
    llm_first_by_platform = {
        "douyin": "DOUYIN_LLM_FIRST",
        "shipinhao": "SHIPINHAO_LLM_FIRST",
        "xiaohongshu": "XIAOHONGSHU_LLM_FIRST",
        "bilibili": "BILIBILI_LLM_FIRST",
        "zhihu": "ZHIHU_LLM_FIRST",
    }
    env_key = llm_first_by_platform.get(platform_key)
    if env_key:
        return not _llm_first_enabled(env_key)
    return True


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


def _xhs_url_published(url: str) -> bool:
    """小红书提交成功后常回到 upload 页并在 query 带 published=true。"""
    return "published=true" in (url or "").lower()


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
        for name in (
            "Never allow",
            "Block",
            "阻止",
            "不允许",
            "禁止",
            "不再询问",
        ):
            btn = page.get_by_role("button", name=name).first
            try:
                if await btn.count() and await btn.is_visible():
                    await btn.click(timeout=2000, force=True)
                    await asyncio.sleep(0.5)
            except Exception:
                pass
        for text in ("不允许", "Block", "Never allow"):
            loc = page.get_by_text(text, exact=False).first
            try:
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=2000, force=True)
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

    if platform == "bilibili":
        try:
            loc = page.locator(
                'input[type="file"][accept*="video"], input[type="file"]'
            ).first
            await loc.wait_for(state="attached", timeout=60_000)
            await loc.set_input_files(str(video_path))
            print(f"  [upload] B站已选择视频: {video_path.name}", flush=True)
            await asyncio.sleep(3)
            return True
        except Exception as exc:
            print(f"  [upload] B站专用上传失败: {exc}", flush=True)

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
        "bilibili": ("上传视频", "点击上传", "上传"),
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


def _build_system_prompt(platform: str, *, platform_key: str = "") -> str:
    base = f"""你是真人运营，在 {platform} 创作者后台发视频。根据页面元素（和截图）每次只输出**一步** JSON。
像人一样：先 wait 等上传/加载，再 click/type；不要连点同一按钮；打字用 type 逐段输入。
字段：thought, action, ref, text, wait_seconds, reason
action：click | type | wait | press_key | scroll | done | need_human
click/type **必须**带 ref（元素列表里的数字）。若目标是点「发布/投稿/立即投稿」可省略 ref，系统会用脚本代点。
卡住或验证码时用 need_human。只输出 JSON，不要 markdown。"""
    if platform_key == "xiaohongshu":
        base += """

小红书视频发布极简流程（严格遵守）：
只做：上传视频 → 正文描述 type → 发布 click。
不要填标题、不要动封面、**不要勾选原创声明**；要发布时 click 可省略 ref，系统会滚到底并代点发布。"""
    return base


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
        if _xhs_url_published(url):
            return True
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
        # 仍在编辑页但 toast/弹层已提示成功（URL 常不变）
        for text in (
            "发布成功",
            "发布完成",
            "笔记发布成功",
            "已发布",
            "已提交",
            "提交成功",
        ):
            if text in state.body_snippet:
                return True

    if platform_key == "bilibili":
        if "member.bilibili.com" in url and (
            "upload-manager" in url or "manage" in url
        ):
            return True
        for text in ("投稿成功", "稿件投递成功", "提交成功", "已提交审核"):
            if text in state.body_snippet:
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
    if still_uploading and not (
        platform_key == "xiaohongshu" and _xhs_url_published(url)
    ):
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


def _parse_bilibili_tags(raw: str) -> list[str]:
    return [t.strip().lstrip("#") for t in str(raw or "").split(",") if t.strip()][:12]


BILIBILI_TID_PARTITION: dict[int, tuple[str, str]] = {
    207: ("财经", "财经杂谈"),
    208: ("财经", "财经综合"),
    209: ("知识", "社科"),
    124: ("知识", "科学科普"),
    229: ("科技", "软件应用"),
}


async def _bilibili_page_body(page) -> str:
    try:
        return await page.evaluate(
            "() => (document.body && document.body.innerText) || ''"
        )
    except Exception:
        return ""


def _bilibili_upload_complete(body: str) -> bool:
    if any(t in body for t in ("上传完成", "上传成功")):
        return True
    if "100%" in body:
        return True
    percents = [int(m.group(1)) for m in re.finditer(r"(\d+)\s*%", body)]
    if percents and max(percents) >= 100:
        return True
    if any(t in body for t in ("上传中", "正在上传", "等待上传", "上传失败")):
        return False
    if percents and max(percents) < 100:
        return False
    return False


BILIBILI_MANAGE_URL = (
    "https://member.bilibili.com/platform/upload-manager/article"
)


def _bilibili_viewport() -> dict[str, int]:
    try:
        w = int(_env("BILIBILI_BROWSER_WIDTH", "1440"))
        h = int(_env("BILIBILI_BROWSER_HEIGHT", "2000"))
    except ValueError:
        w, h = 1440, 2000
    return {"width": w, "height": h}


def _bilibili_use_maximized_window() -> bool:
    raw = _env("BILIBILI_BROWSER_MAXIMIZED", "1").lower()
    return raw not in ("0", "false", "no", "off")


async def bilibili_prepare_page(page) -> None:
    """加高视口/最大化并滚到底，确保底部「立即投稿」栏可见。"""
    if _bilibili_use_maximized_window():
        try:
            await page.evaluate(
                """() => {
                try {
                  window.moveTo(0, 0);
                  window.resizeTo(screen.availWidth, screen.availHeight);
                } catch (e) {}
            }"""
            )
        except Exception:
            pass
    else:
        vp = _bilibili_viewport()
        try:
            await page.set_viewport_size(vp)
        except Exception:
            pass
    await _bilibili_scroll_to_footer(page)
    vp = _bilibili_viewport()
    mode = "最大化" if _bilibili_use_maximized_window() else f"{vp['width']}x{vp['height']}"
    print(f"  [script] B站页面 {mode}，已滚至底部", flush=True)


async def _bilibili_scroll_to_footer(page) -> None:
    await page.evaluate(
        """() => {
        const scrollables = [...document.querySelectorAll('*')].filter(el => {
          const st = getComputedStyle(el);
          return (st.overflowY === 'auto' || st.overflowY === 'scroll')
            && el.scrollHeight > el.clientHeight + 40;
        }).sort((a, b) =>
          (b.scrollHeight - b.clientHeight) - (a.scrollHeight - a.clientHeight)
        );
        for (const el of scrollables.slice(0, 5)) {
          el.scrollTop = el.scrollHeight;
        }
        for (const sel of [
          '.center-module', '[class*="content-scroll"]', '[class*="upload"]',
          'main', '[class*="container"]',
        ]) {
          for (const el of document.querySelectorAll(sel)) {
            if (el.scrollHeight > el.clientHeight + 40) {
              el.scrollTop = el.scrollHeight;
            }
          }
        }
        window.scrollTo(0, Math.max(
          document.body.scrollHeight,
          document.documentElement.scrollHeight
        ));
    }"""
    )
    try:
        box = await page.evaluate(
            """() => {
            const el = document.elementFromPoint(
              Math.floor(window.innerWidth * 0.55),
              Math.floor(window.innerHeight * 0.45)
            );
            const r = (el || document.body).getBoundingClientRect();
            return { x: r.x + r.width / 2, y: r.y + r.height / 2 };
        }"""
        )
        await page.mouse.move(box["x"], box["y"])
    except Exception:
        pass
    for _ in range(12):
        try:
            await page.mouse.wheel(0, 900)
            await page.keyboard.press("End")
        except Exception:
            pass
        await asyncio.sleep(0.12)


def _xhs_viewport() -> dict[str, int]:
    try:
        w = int(_env("XHS_BROWSER_WIDTH", "1440"))
        h = int(_env("XHS_BROWSER_HEIGHT", "2000"))
    except ValueError:
        w, h = 1440, 2000
    return {"width": w, "height": h}


def _xhs_use_maximized_window() -> bool:
    raw = _env("XHS_BROWSER_MAXIMIZED", "1").lower()
    return raw not in ("0", "false", "no", "off")


async def _xhs_scroll_to_publish(page) -> None:
    await page.evaluate(
        """() => {
        for (const sel of [
          '.publish-container', '[class*="publish-container"]',
          '[class*="Publish"]', 'main', '[class*="content"]',
        ]) {
          for (const el of document.querySelectorAll(sel)) {
            if (el.scrollHeight > el.clientHeight + 40) {
              el.scrollTop = el.scrollHeight;
            }
          }
        }
        window.scrollTo(0, Math.max(
          document.body.scrollHeight,
          document.documentElement.scrollHeight
        ));
    }"""
    )
    for _ in range(10):
        try:
            await page.keyboard.press("End")
            await page.mouse.wheel(0, 800)
        except Exception:
            pass
        await asyncio.sleep(0.12)


async def xhs_prepare_page(page) -> None:
    """最大化/加高视口并滚到底，确保底部「发布」按钮可见。"""
    if _xhs_use_maximized_window():
        try:
            await page.evaluate(
                """() => {
                try {
                  window.moveTo(0, 0);
                  window.resizeTo(screen.availWidth, screen.availHeight);
                } catch (e) {}
            }"""
            )
        except Exception:
            pass
    else:
        vp = _xhs_viewport()
        try:
            await page.set_viewport_size(vp)
        except Exception:
            pass
    await dismiss_overlays(page, platform_key="xiaohongshu")
    await _xhs_disable_pk_cover(page)
    await _xhs_scroll_to_publish(page)
    vp = _xhs_viewport()
    mode = "最大化" if _xhs_use_maximized_window() else f"{vp['width']}x{vp['height']}"
    print(f"  [script] 小红书页面 {mode}，已滚至底部", flush=True)


async def _bilibili_submit_errors(page) -> list[str]:
    body = await _bilibili_page_body(page)
    hints = (
        "请选择分区",
        "请填写标题",
        "请添加标签",
        "上传未完成",
        "视频上传中",
        "不能为空",
        "稿件标题",
        "请先上传",
        "请选择符合您视频内容的创作声明",
    )
    errors = [h for h in hints if h in body]
    if await _bilibili_declaration_pending(page):
        errors.append("创作声明未选择")
    return errors


async def _bilibili_pick_any_partition(page) -> bool:
    """分区选不上时：尽量点选第一个可用分区（比 vlog 空着更易过校验）。"""
    for loc in (
        page.get_by_text("请选择分区", exact=False).first,
        page.locator('[class*="video-type"]').first,
        page.locator('[class*="type-select"]').first,
        page.get_by_text("分区", exact=True).first,
    ):
        if not await loc.count():
            continue
        try:
            if await loc.is_visible():
                await loc.click(timeout=5000)
                await asyncio.sleep(1)
                break
        except Exception:
            continue
    else:
        return False
    for sel in (
        '[class*="type-item"]',
        '[class*="select-item"]',
        '[class*="option"]',
        '[role="option"]',
        'li[class*="item"]',
    ):
        items = page.locator(sel)
        count = await items.count()
        for i in range(min(count, 8)):
            item = items.nth(i)
            try:
                text = (await item.inner_text()).strip()
                if not text or len(text) > 20:
                    continue
                if await item.is_visible():
                    await item.click(timeout=5000)
                    print(f"  [script] B站已选分区: {text}", flush=True)
                    await asyncio.sleep(0.5)
                    return True
            except Exception:
                continue
    return False


async def _wait_bilibili_upload_ready(page, *, timeout_s: int = 600) -> None:
    last_pct = -1
    for _ in range(timeout_s // 2):
        body = await _bilibili_page_body(page)
        if _bilibili_upload_complete(body):
            print("  [script] B站视频上传完成", flush=True)
            await asyncio.sleep(1)
            return
        percents = [int(m.group(1)) for m in re.finditer(r"(\d+)\s*%", body)]
        pct = max(percents) if percents else -1
        if 0 <= pct != last_pct:
            print(f"  [script] B站上传进度 {pct}%…", flush=True)
            last_pct = pct
        await asyncio.sleep(2)
    raise LLMBrowserError("B站视频上传超时")


async def _bilibili_select_partition(page, *, tid: int = 207) -> None:
    """可选：默认跳过分区（vlog 等默认分区也可投稿）。"""
    raw = _env("BILIBILI_SELECT_PARTITION", "0").lower()
    if raw not in ("1", "true", "yes", "on"):
        print("  [script] 跳过分区选择（使用页面默认分区）", flush=True)
        return

    parent_kw, child_kw = BILIBILI_TID_PARTITION.get(tid, ("财经", "财经杂谈"))
    body = await _bilibili_page_body(page)
    body_lower = body.lower()
    if child_kw in body and "vlog" not in body_lower:
        return
    if parent_kw in body and "vlog" not in body_lower and "请选择分区" not in body:
        return

    opened = False
    for loc in (
        page.locator('[class*="video-type"]').first,
        page.locator('[class*="type-select"]').first,
        page.locator('[class*="select-type"]').first,
        page.get_by_text("请选择分区", exact=False).first,
        page.get_by_text("分区", exact=True).first,
    ):
        if not await loc.count():
            continue
        try:
            if await loc.is_visible():
                await loc.click(timeout=5000)
                opened = True
                break
        except Exception:
            continue
    if not opened:
        for hint in ("vlog", "Vlog", "日常", "生活"):
            chip = page.get_by_text(hint, exact=False).first
            if not await chip.count():
                continue
            try:
                if await chip.is_visible():
                    await chip.click(timeout=5000)
                    opened = True
                    break
            except Exception:
                continue
    if not opened:
        print("  [script] ⚠️ 未找到 B 站分区选择器，继续…", flush=True)
        return

    await asyncio.sleep(1)
    search = page.locator(
        'input[placeholder*="搜索"], input[placeholder*="分区"], input[class*="search"]'
    ).first
    if await search.count():
        try:
            await search.fill(child_kw)
            await asyncio.sleep(0.8)
        except Exception:
            pass

    for kw in (child_kw, parent_kw):
        opt = page.get_by_text(kw, exact=False).first
        if not await opt.count():
            continue
        try:
            if await opt.is_visible():
                await opt.click(timeout=5000)
                print(f"  [script] B站分区已选: {kw}", flush=True)
                await asyncio.sleep(0.5)
                return
        except Exception:
            continue
    print(f"  [script] ⚠️ B站分区未选中（目标 tid={tid}），继续…", flush=True)


_DECLARATION_PLACEHOLDER = "请选择符合您视频内容的创作声明"
_DECLARATION_OPTIONS = (
    "含AI生成内容",
    "内容无需标注",
    "含虚构演绎内容",
    "内容含营销信息",
    "个人观点，仅供参考",
    "内容为转载",
)


def _bilibili_creation_declaration_choices() -> list[str]:
    raw = _env("BILIBILI_CREATION_DECLARATION", "").strip()
    if raw:
        first = raw.split("|")[0].strip()
        if first:
            return [first]
    return ["含AI生成内容"]


_DECLARATION_INDEX = {
    "内容无需标注": 0,
    "含AI生成内容": 1,
    "含虚构演绎内容": 2,
    "内容含营销信息": 3,
    "个人观点，仅供参考": 4,
    "内容为转载": 5,
}


def _declaration_choice_variants(choice: str) -> list[str]:
    variants = [choice]
    if "AI" in choice or "ai" in choice.lower():
        variants.extend(["含AI生成内容", "含AI生成", "AI生成"])
    for opt in _DECLARATION_OPTIONS:
        if opt not in variants and (choice in opt or opt in choice):
            variants.append(opt)
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


async def _bilibili_declaration_selected(page) -> str:
    try:
        picked = await page.evaluate(
            """(ph) => {
            for (const lab of document.querySelectorAll('*')) {
              if ((lab.innerText || '').trim() !== '创作声明') continue;
              let node = lab.parentElement;
              for (let d = 0; d < 10 && node; d++, node = node.parentElement) {
                const tr = node.querySelector('[class*="select-selector"]');
                if (!tr) continue;
                const t = (tr.innerText || '').trim().split('\\n')[0].trim();
                if (!t || t.includes(ph) || t === '创作声明') continue;
                return t;
              }
            }
            for (const inp of document.querySelectorAll('input')) {
              const ph2 = inp.getAttribute('placeholder') || '';
              if (!ph2.includes('创作声明') && !ph2.includes('请选择符合')) continue;
              const v = (inp.value || '').trim();
              if (v && !v.includes(ph)) return v;
            }
            return '';
        }""",
            _DECLARATION_PLACEHOLDER,
        )
        text = str(picked or "").strip()
        if text:
            return text
    except Exception:
        pass
    try:
        inp = page.locator(
            'input[placeholder*="请选择符合"], input[placeholder*="创作声明"]'
        ).first
        if await inp.count():
            val = (await inp.input_value()).strip()
            if val and _DECLARATION_PLACEHOLDER not in val:
                return val
    except Exception:
        pass
    return ""


async def _bilibili_declaration_pending(page) -> bool:
    return not bool(await _bilibili_declaration_selected(page))


async def _bilibili_open_declaration_dropdown_pw(page) -> bool:
    for loc in (
        page.locator(f'input[placeholder="{_DECLARATION_PLACEHOLDER}"]'),
        page.locator('input[placeholder*="请选择符合您视频"]'),
        page.locator('input[placeholder*="创作声明"]'),
        page.get_by_text(_DECLARATION_PLACEHOLDER, exact=True),
    ):
        if not await loc.count():
            continue
        try:
            target = loc.first
            await target.scroll_into_view_if_needed(timeout=8000)
            await target.click(timeout=8000, force=True)
            await asyncio.sleep(0.6)
            return True
        except Exception:
            continue
    label = page.get_by_text("创作声明", exact=True).first
    if await label.count():
        try:
            row = label.locator(
                'xpath=ancestor::div[contains(@class,"form") or contains(@class,"item")'
                ' or contains(@class,"field")][1]'
            )
            trigger = row.locator(
                '[class*="select-selector"], [class*="bcc-select"], [class*="select"]'
            ).first
            if await trigger.count():
                await trigger.click(timeout=8000, force=True)
                await asyncio.sleep(0.6)
                return True
        except Exception:
            pass
    return await _bilibili_open_declaration_dropdown_js(page)


async def _bilibili_click_declaration_option_pw(page, choices: list[str]) -> bool:
    for choice in choices:
        for loc in (
            page.locator(f'[class*="select-dropdown"] >> text="{choice}"'),
            page.locator(f'[class*="bcc-select-dropdown"] >> text="{choice}"'),
            page.locator('[class*="popover"], [class*="dropdown"]').get_by_text(
                choice, exact=True
            ),
        ):
            if not await loc.count():
                continue
            for i in range(min(await loc.count(), 5)):
                item = loc.nth(i)
                try:
                    if not await item.is_visible():
                        continue
                    await item.click(timeout=5000, force=True)
                    await asyncio.sleep(0.5)
                    if not await _bilibili_declaration_pending(page):
                        return True
                except Exception:
                    continue
        items = page.get_by_text(choice, exact=True)
        for i in range(min(await items.count(), 8)):
            item = items.nth(i)
            try:
                if not await item.is_visible():
                    continue
                await item.click(timeout=5000, force=True)
                await asyncio.sleep(0.5)
                if not await _bilibili_declaration_pending(page):
                    return True
            except Exception:
                continue
    return False


async def _bilibili_open_declaration_dropdown_js(page) -> bool:
    try:
        return bool(
            await page.evaluate(
                """(ph) => {
                for (const lab of document.querySelectorAll('*')) {
                  if ((lab.innerText || '').trim() !== '创作声明') continue;
                  let node = lab.parentElement;
                  for (let d = 0; d < 10 && node; d++, node = node.parentElement) {
                    const tr = node.querySelector('[class*="select-selector"]');
                    if (tr) { tr.click(); return true; }
                  }
                }
                for (const el of document.querySelectorAll('*')) {
                  const t = (el.innerText || '').trim();
                  if (t === ph && el.offsetParent !== null) { el.click(); return true; }
                }
                return false;
            }""",
                _DECLARATION_PLACEHOLDER,
            )
        )
    except Exception:
        return False


async def _bilibili_click_declaration_option_js(page, choice: str) -> bool:
    try:
        return bool(
            await page.evaluate(
                """(choice) => {
                const match = (n) => (n.innerText || '').trim() === choice;
                const visible = (el) => {
                  const r = el.getBoundingClientRect();
                  const st = getComputedStyle(el);
                  return r.width > 8 && r.height > 8
                    && st.display !== 'none' && st.visibility !== 'hidden';
                };
                for (const root of document.querySelectorAll(
                  '[class*="select-dropdown"], [class*="bcc-select-dropdown"], [class*="popover"]'
                )) {
                  for (const n of root.querySelectorAll('div, li, span')) {
                    if (match(n) && visible(n)) { n.click(); return true; }
                  }
                }
                return false;
            }""",
                choice,
            )
        )
    except Exception:
        return False


async def _bilibili_keyboard_pick_declaration(page, choice: str) -> bool:
    if not await _bilibili_open_declaration_dropdown_pw(page):
        return False
    await asyncio.sleep(0.5)
    idx = _DECLARATION_INDEX.get(choice, 1)
    for _ in range(idx + 1):
        await page.keyboard.press("ArrowDown")
    await page.keyboard.press("Enter")
    await asyncio.sleep(0.4)
    return not await _bilibili_declaration_pending(page)


async def _bilibili_fill_creation_declaration(page) -> bool:
    """B 站必填「创作声明」：多策略重试。"""
    if not await _bilibili_declaration_pending(page):
        selected = await _bilibili_declaration_selected(page)
        print(f"  [script] B站创作声明已填: {selected}，跳过", flush=True)
        return True

    preferred = _bilibili_creation_declaration_choices()[0]
    variants = _declaration_choice_variants(preferred)
    print(f"  [script] 选择 B 站创作声明: {preferred}", flush=True)

    label = page.get_by_text("创作声明", exact=True).first
    if await label.count():
        try:
            await label.scroll_into_view_if_needed(timeout=8000)
        except Exception:
            pass

    for attempt in range(1, 4):
        opened = await _bilibili_open_declaration_dropdown_pw(page)
        if not opened:
            await asyncio.sleep(0.5)
            continue
        await asyncio.sleep(0.5)
        if await _bilibili_click_declaration_option_pw(page, variants):
            break
        for choice in variants:
            if await _bilibili_click_declaration_option_js(page, choice):
                await asyncio.sleep(0.4)
                break
            if await _bilibili_keyboard_pick_declaration(page, choice):
                break
        if not await _bilibili_declaration_pending(page):
            break
        print(f"  [script] 创作声明第 {attempt} 次未选中，重试…", flush=True)
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        await asyncio.sleep(0.4)

    if await _bilibili_declaration_pending(page):
        print("  [script] ⚠️ B站创作声明未选中", flush=True)
        return False

    selected = await _bilibili_declaration_selected(page)
    print(f"  [script] B站创作声明已选: {selected or preferred}", flush=True)
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass
    return True


async def _bilibili_remove_unwanted_tags(page, keep: list[str]) -> None:
    keep_lower = {k.lower() for k in keep}
    for _ in range(20):
        chips = page.locator('[class*="label-item"], [class*="tag-item-v2"]')
        count = await chips.count()
        removed = False
        for i in range(count):
            chip = chips.nth(i)
            try:
                text = (await chip.inner_text()).strip().lstrip("#").split("\n")[0]
            except Exception:
                continue
            if not text or text.lower() in keep_lower:
                continue
            close = chip.locator(
                '[class*="close"], [class*="delete"], [class*="icon-close"]'
            ).first
            if not await close.count():
                continue
            try:
                await close.click(timeout=3000)
                removed = True
                await asyncio.sleep(0.35)
                break
            except Exception:
                continue
        if not removed:
            break


async def _bilibili_fill_tags_only(
    page, tags: list[str], cfg: AgentConfig
) -> int:
    """批量填写 B 站标签（本地脚本，不消耗 LLM 步数）。"""
    if not tags:
        return 0
    await _bilibili_remove_unwanted_tags(page, tags)
    existing_body = await _bilibili_page_body(page)
    added = 0
    for tag in tags[:12]:
        if tag.lower() in existing_body.lower():
            continue
        tag_in = page.locator(
            'input[placeholder*="标签"], input[placeholder*="回车"], input[placeholder*="Enter"]'
        ).first
        if not await tag_in.count():
            break
        try:
            await tag_in.click(timeout=5000)
            await tag_in.fill(tag)
            await page.keyboard.press("Enter")
            await asyncio.sleep(0.25)
            existing_body = await _bilibili_page_body(page)
            added += 1
        except Exception:
            break
    return added


async def _bilibili_try_autofill_tags(
    page, tags: list[str], cfg: AgentConfig
) -> bool:
    """上传完成后一次性批量填标签，避免 LLM 逐步 press_key。"""
    if not tags:
        return False
    body = await _bilibili_page_body(page)
    if not _bilibili_upload_complete(body):
        return False
    missing = [t for t in tags[:12] if t.lower() not in body.lower()]
    if not missing:
        return False
    added = await _bilibili_fill_tags_only(page, tags, cfg)
    if added:
        print(f"  [script] B站已批量添加 {added} 个标签（LLM 无需再逐个添加）", flush=True)
        return True
    return False


async def _bilibili_fill_form(
    page, *, title: str, desc: str, tags: list[str], cfg: AgentConfig
) -> None:
    tin = page.locator(
        'input[placeholder*="标题"], input[placeholder*="请输入"], input[maxlength="80"]'
    ).first
    await tin.wait_for(state="visible", timeout=120_000)
    await tin.click(timeout=8000)
    await page.keyboard.press("Control+A")
    await page.keyboard.press("Backspace")
    await tin.fill(title[:80])

    if not await _bilibili_fill_creation_declaration(page):
        print("  [script] ❌ 创作声明未填，后续投稿将失败", flush=True)

    filled_desc = False
    for sel in (
        "div.ql-editor",
        '[contenteditable="true"]',
        "textarea",
    ):
        ed = page.locator(sel).first
        if not await ed.count():
            continue
        try:
            await ed.click(timeout=8000)
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await human_type(page, desc[:2000], cfg)
            filled_desc = True
            break
        except Exception:
            continue
    if not filled_desc:
        print("  [script] ⚠️ B站简介区未找到，继续…", flush=True)

    await _bilibili_fill_tags_only(page, tags, cfg)
    print("  [script] 已填写 B 站标题/简介/标签", flush=True)


async def _bilibili_publish_succeeded(page) -> bool:
    url = (page.url or "").lower()
    if "upload-manager" in url or "/manage" in url:
        return True
    body = await _bilibili_page_body(page)
    if any(
        t in body
        for t in ("投稿成功", "提交成功", "稿件投递成功", "已提交审核", "稿件投递完成")
    ):
        return True
    # 仍在上传编辑页且无成功提示 → 未成功
    if "upload/video" in url or "upload/video/frame" in url:
        return False
    return False


async def _bilibili_verify_in_manage(page, title: str) -> bool:
    snippet = (title or "").strip()[:16]
    if not snippet:
        return False
    try:
        await page.goto(
            BILIBILI_MANAGE_URL,
            wait_until="domcontentloaded",
            timeout=90_000,
        )
        await asyncio.sleep(2)
        body = await _bilibili_page_body(page)
        if snippet in body:
            print(f"  [script] 已在稿件管理页找到: {snippet}", flush=True)
            return True
    except Exception as exc:
        print(f"  [script] ⚠️ 校验稿件管理页失败: {exc}", flush=True)
    return False


async def _bilibili_wait_submit_success(
    page, *, title: str = "", timeout_s: int = 45
) -> bool:
    for i in range(timeout_s // 2):
        await _bilibili_handle_confirm_dialog(page)
        if await _bilibili_publish_succeeded(page):
            return True
        errors = await _bilibili_submit_errors(page)
        if errors:
            print(f"  [script] ⚠️ B站页面提示: {', '.join(errors)}", flush=True)
        if i > 0 and i % 5 == 0:
            print(f"  [script] 等待 B 站投稿结果… ({i * 2}s)", flush=True)
        await asyncio.sleep(2)
    if title and await _bilibili_verify_in_manage(page, title):
        return True
    return False


async def _bilibili_handle_confirm_dialog(page) -> bool:
    clicked = False
    for text in ("确认投稿", "确定投稿", "确认", "确定", "提交"):
        for btn in (
            page.get_by_role("button", name=text).first,
            page.locator(f'button:has-text("{text}")').first,
            page.locator(f'div[role="button"]:has-text("{text}")').first,
            page.locator(f'[class*="modal"] button:has-text("{text}")').first,
            page.locator(f'[class*="dialog"] button:has-text("{text}")').first,
        ):
            if not await btn.count():
                continue
            try:
                if await btn.is_visible():
                    await btn.click(timeout=8000)
                    clicked = True
                    await asyncio.sleep(1.5)
            except Exception:
                continue
    return clicked


async def _bilibili_find_submit_button(page):
    await _bilibili_scroll_to_footer(page)
    handle = await page.evaluate_handle(
        """() => {
        const labels = ['立即投稿', '投稿'];
        const nodes = [...document.querySelectorAll(
          'button, [role="button"], div[class*="submit"], span[class*="submit"], a'
        )];
        const score = (n, label) => {
          const t = (n.innerText || n.textContent || '').trim();
          if (!t || !t.includes(label)) return -1;
          const r = n.getBoundingClientRect();
          if (r.width < 20 || r.height < 10) return -1;
          const st = getComputedStyle(n);
          let s = 0;
          if (t === label || t === '立即投稿') s += 50;
          if (st.position === 'fixed' || st.position === 'sticky') s += 40;
          if (r.bottom >= window.innerHeight - 160) s += 30;
          if (r.top >= window.innerHeight * 0.55) s += 10;
          return s;
        };
        let best = null, bestScore = -1;
        for (const label of labels) {
          for (const n of nodes) {
            const sc = score(n, label);
            if (sc > bestScore) { bestScore = sc; best = n; }
          }
        }
        return best;
    }"""
    )
    try:
        element = handle.as_element()
        if element is not None:
            return element
    except Exception:
        pass

    for sel in (
        'button:has-text("立即投稿")',
        'button:has-text("投稿")',
        'div[class*="submit-add"]',
        'span:has-text("立即投稿")',
        ".submit-add",
        '[class*="submit-add"]',
        '[class*="submit-container"] button',
        '[class*="footer"] button:has-text("投稿")',
    ):
        btn = page.locator(sel).last
        if not await btn.count():
            continue
        try:
            if await btn.is_visible():
                return btn
        except Exception:
            continue
    for name in ("立即投稿", "投稿", "发布"):
        btn = page.get_by_role("button", name=name).last
        if await btn.count():
            try:
                if await btn.is_visible():
                    return btn
            except Exception:
                continue
    return None


async def _bilibili_click_submit(page, *, title: str = "") -> bool:
    async def _try_once() -> bool:
        await bilibili_prepare_page(page)
        if not await _bilibili_declaration_selected(page):
            if not await _bilibili_fill_creation_declaration(page):
                print("  [script] ❌ 创作声明未填，无法投稿", flush=True)
                return False
        btn = None
        max_wait = int(_env("BILIBILI_SUBMIT_WAIT_ROUNDS", "15"))
        for i in range(max_wait):
            btn = await _bilibili_find_submit_button(page)
            if btn:
                try:
                    if not await btn.is_disabled():
                        break
                except Exception:
                    break
                if i >= 5:
                    print("  [script] 投稿按钮仍 disabled，将尝试强制点击", flush=True)
                    break
            if i > 0 and i % 5 == 0:
                print(f"  [script] 等待投稿按钮… ({i * 2}s)", flush=True)
            await asyncio.sleep(2)

        if not btn:
            clicked = await page.evaluate(
                """() => {
                for (const label of ['立即投稿', '投稿']) {
                  const nodes = [...document.querySelectorAll('button, [role="button"], div, span')];
                  const el = nodes.find(n => (n.innerText||'').trim().includes(label));
                  if (el) { el.click(); return label; }
                }
                return '';
            }"""
            )
            if not clicked:
                print("  [script] ⚠️ 未找到 B 站投稿按钮", flush=True)
                return False
            print(f"  [script] 已通过 JS 点击 B 站「{clicked}」", flush=True)
        else:
            clicked = False
            for force in (False, True):
                try:
                    await btn.scroll_into_view_if_needed(timeout=10_000)
                    await btn.click(timeout=15_000, force=force)
                    print(
                        f"  [script] 已点击 B 站投稿按钮（force={force}）",
                        flush=True,
                    )
                    clicked = True
                    break
                except Exception as exc:
                    if force:
                        print(f"  [script] ⚠️ 投稿按钮点击失败: {exc}", flush=True)
                    continue
            if not clicked:
                try:
                    await btn.evaluate("el => el.click()")
                    print("  [script] 已通过 JS 点击投稿按钮", flush=True)
                except Exception as exc:
                    print(f"  [script] ⚠️ JS 点击投稿失败: {exc}", flush=True)
                    return False

        await asyncio.sleep(2)
        await _bilibili_handle_confirm_dialog(page)
        return await _bilibili_wait_submit_success(page, title=title)

    if await _try_once():
        print("  [script] 检测到 B 站投稿成功", flush=True)
        return True

    errors = await _bilibili_submit_errors(page)
    if any("分区" in e for e in errors):
        print("  [script] 尝试自动选择任意分区后重试投稿…", flush=True)
        if await _bilibili_pick_any_partition(page):
            if await _try_once():
                print("  [script] 检测到 B 站投稿成功（重试后）", flush=True)
                return True

    errors = await _bilibili_submit_errors(page)
    if errors:
        print(
            f"  [script] ❌ B站投稿未成功，页面提示: {', '.join(errors)}",
            flush=True,
        )
    else:
        print(
            "  [script] ❌ B站投稿未成功（仍在上传页，后台不会出现新稿件）",
            flush=True,
        )
    return False


async def _bilibili_form_ready(page) -> bool:
    title = page.locator(
        'input[placeholder*="标题"], input[placeholder*="请输入"], input[maxlength="80"]'
    ).first
    if not await title.count():
        return False
    try:
        return await title.is_visible()
    except Exception:
        return False


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
    """已禁用：不勾选原创声明（易误触且非必需）。"""
    return False


async def _xhs_publish_succeeded(page) -> bool:
    url = (page.url or "").lower()
    if _xhs_url_published(url):
        return True
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

    await xhs_prepare_page(page)

    for tick in range(30):
        if tick > 0 and tick % 10 == 0:
            print(f"  [script] 仍在等待小红书发布… ({tick}s)", flush=True)
        await dismiss_overlays(page, platform_key="xiaohongshu")
        await _xhs_disable_pk_cover(page)
        await _xhs_scroll_to_publish(page)

        if await _xhs_publish_succeeded(page):
            print("  [script] 检测到发布成功", flush=True)
            return True

        try:
            js_clicked = await page.evaluate(
                """() => {
                const labels = ['立即发布', '发布'];
                const candidates = [];
                for (const n of document.querySelectorAll(
                  'button, [role="button"], a, div, span'
                )) {
                  const t = (n.innerText || '').trim();
                  if (!t) continue;
                  for (const label of labels) {
                    if (t !== label && !t.startsWith(label)) continue;
                    const r = n.getBoundingClientRect();
                    if (r.width < 30 || r.height < 16) continue;
                    candidates.push({ n, r, label, score: r.bottom + r.width });
                  }
                }
                candidates.sort((a, b) => b.score - a.score);
                for (const c of candidates) {
                  if (c.r.bottom < 0 || c.r.top > window.innerHeight + 2) continue;
                  try {
                    c.n.scrollIntoView({ block: 'center' });
                    c.n.click();
                    return c.label;
                  } catch (e) {}
                }
                return '';
            }"""
            )
        except Exception as exc:
            print(f"  [script] JS 点击发布异常: {exc}", flush=True)
            js_clicked = ""
        if js_clicked:
            print(f"  [script] 已通过 JS 点击小红书「{js_clicked}」", flush=True)
            clicked = True

        if not clicked:
            for name in ("立即发布", "发布"):
                for btn in (
                    page.locator('[class*="footer"] button').filter(has_text=name).last,
                    page.locator('[class*="publish"] button').filter(has_text=name).last,
                    page.locator('[class*="submit"]').filter(has_text=name).last,
                    page.get_by_role("button", name=name, exact=True),
                    page.locator(f'button:text-is("{name}")'),
                    page.locator(".publish-container").locator(
                        f'button:has-text("{name}")'
                    ).last,
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
                        print(f"  [script] 已点击小红书「{name}」", flush=True)
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
            print("  [script] 检测到发布成功", flush=True)
            return True

    if clicked:
        for wait in range(8):
            await asyncio.sleep(1)
            if await _xhs_publish_succeeded(page):
                print("  [script] 检测到发布成功", flush=True)
                return True
        print("  [script] 已点击发布，视为已提交", flush=True)
        return True
    print("  [script] ⚠️ 未找到小红书发布按钮（请检查视口是否最大化）", flush=True)
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

    if platform_key == "bilibili":
        await _wait_bilibili_upload_ready(page)
        return


async def _llm_publish_success(
    page,
    *,
    success_patterns: list[str],
    start_url: str,
    platform_key: str,
    history: list[str],
    steps: int,
    llm_calls: int,
) -> dict[str, Any] | None:
    for _ in range(5):
        await asyncio.sleep(1)
        state = await extract_page_state(page, screenshot_path=None)
        if _check_success(
            state, success_patterns, start_url=start_url, platform_key=platform_key
        ):
            return {
                "ok": True,
                "steps": steps,
                "llm_calls": llm_calls,
                "url": state.url,
                "history": history,
            }
    if platform_key == "xiaohongshu" and _xhs_url_published(page.url):
        return {
            "ok": True,
            "steps": steps,
            "llm_calls": llm_calls,
            "url": page.url,
            "history": [*history, "published_query"],
        }
    return None


async def _llm_ready_to_publish(page, platform_key: str) -> bool:
    if platform_key == "bilibili":
        body = await _bilibili_page_body(page)
        if not _bilibili_upload_complete(body):
            return False
        if await _bilibili_declaration_pending(page):
            return False
        return True
    return platform_key in ("douyin", "shipinhao")


async def _llm_assist_prefill(
    page, *, platform_key: str, fields: dict[str, Any], cfg: AgentConfig
) -> bool:
    """LLM 模式：脚本预填可靠字段（不点发布）。小红书仅等待上传，禁止脚本填表。"""
    if platform_key == "xiaohongshu":
        try:
            await _wait_xhs_video_uploaded(page)
            await dismiss_overlays(page, platform_key="xiaohongshu")
            await _xhs_disable_pk_cover(page)
            print("  [script] 小红书视频已就绪，后续由 LLM 逐步填表", flush=True)
            return True
        except Exception as exc:
            print(f"  [script] 小红书等待上传: {exc}", flush=True)
            return False

    if not fields:
        return False

    try:
        if platform_key == "bilibili":
            await _wait_bilibili_upload_ready(page)
            await bilibili_prepare_page(page)
            tid = int(fields.get("tid") or 207)
            await _bilibili_select_partition(page, tid=tid)
            await _bilibili_fill_form(
                page,
                title=str(fields.get("title") or "")[:80],
                desc=str(fields.get("desc") or ""),
                tags=_parse_bilibili_tags(str(fields.get("tags") or "")),
                cfg=cfg,
            )
            print(
                "  [script] B站已脚本预填（标题/简介/创作声明/标签），LLM 仅需投稿",
                flush=True,
            )
            return True

        if platform_key == "douyin":
            await _wait_video_ready(page, "douyin")
            from douyin_publisher import _dismiss_overlays, _fill_form

            await _dismiss_overlays(page)
            await _fill_form(
                page,
                str(fields.get("title") or "")[:30],
                str(fields.get("desc") or ""),
                _parse_tags(str(fields.get("tags") or "")),
            )
            print("  [script] 抖音已脚本预填，LLM 仅需点发布", flush=True)
            return True

        if platform_key == "shipinhao":
            await _wait_shipinhao_video_uploaded(page)
            await dismiss_overlays(page, platform_key="shipinhao")
            tags = _parse_tags(str(fields.get("tags") or ""))
            tag_line = " ".join(f"#{t}" for t in tags)
            body = f"{fields.get('desc') or ''} {tag_line}".strip()
            await _shipinhao_fill_form(page, body, cfg)
            await _shipinhao_declare_original(page)
            print("  [script] 视频号已脚本预填，LLM 仅需点发表", flush=True)
            return True
    except Exception as exc:
        print(f"  [script] {platform_key} 预填未完成，LLM 继续: {exc}", flush=True)
    return False


async def _llm_assist_try_publish(
    page, *, platform_key: str, fields: dict[str, Any], cfg: AgentConfig
) -> bool:
    """脚本尝试点击发布/投稿（不消耗 LLM 步数）。"""
    try:
        if platform_key == "bilibili":
            await bilibili_prepare_page(page)
            return await _bilibili_click_submit(
                page, title=str(fields.get("title") or "")
            )
        if platform_key == "douyin":
            from douyin_publisher import _click_publish, _dismiss_overlays

            await _dismiss_overlays(page)
            return await _click_publish(page, assist=False)
        if platform_key == "shipinhao":
            await dismiss_overlays(page, platform_key="shipinhao")
            return await _shipinhao_click_publish(page)
    except Exception as exc:
        print(f"  [script] {platform_key} 脚本投稿: {exc}", flush=True)
    return False


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
        # 禁止走固定脚本（封号风险），须由 run_agent LLM 逐步发布
        return False

    if platform_key == "douyin":
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

    if platform_key == "bilibili":
        await _wait_bilibili_upload_ready(page)
        await bilibili_prepare_page(page)
        await dismiss_overlays(page, platform_key=platform_key)
        tid = int(fields.get("tid") or 207)
        await _bilibili_select_partition(page, tid=tid)
        bili_title = str(fields.get("title") or "")[:80]
        bili_desc = str(fields.get("desc") or "")
        bili_tags = _parse_bilibili_tags(str(fields.get("tags") or ""))
        await _bilibili_fill_form(
            page, title=bili_title, desc=bili_desc, tags=bili_tags, cfg=cfg
        )
        print("  [script] 正在点击 B 站投稿…", flush=True)
        if await _bilibili_click_submit(page, title=bili_title):
            print("  [script] 已点击 B 站投稿", flush=True)
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


async def _llm_action_fallback(
    page,
    action: dict[str, Any],
    *,
    platform_key: str,
    fields: dict[str, Any],
    cfg: AgentConfig,
) -> str | None:
    """LLM 未给 ref 时，按意图走平台脚本（避免 click 缺少 ref 直接失败）。"""
    thought = str(action.get("thought") or "")
    text = str(action.get("text") or "")
    blob = f"{thought} {text}"

    if platform_key == "bilibili":
        if any(k in blob for k in ("立即投稿", "投稿", "发布", "submit")):
            ok = await _bilibili_click_submit(
                page, title=str(fields.get("title") or "")
            )
            return f"fallback:bilibili_submit ok={ok}"
        if any(k in blob for k in ("创作声明", "声明", "AI生成")):
            ok = await _bilibili_fill_creation_declaration(page)
            return f"fallback:bilibili_declaration ok={ok}"
        if any(k in blob for k in ("滚", "底部", "scroll")):
            await _bilibili_scroll_to_footer(page)
            return "fallback:bilibili_scroll"

    if platform_key == "douyin":
        if any(k in blob for k in ("发布", "提交")):
            from douyin_publisher import _click_publish, _dismiss_overlays

            await _dismiss_overlays(page)
            ok = await _click_publish(page, assist=False)
            return f"fallback:douyin_publish ok={ok}"

    if platform_key == "shipinhao":
        if any(k in blob for k in ("发表", "发布", "提交")):
            await dismiss_overlays(page, platform_key="shipinhao")
            ok = await _shipinhao_click_publish(page)
            return f"fallback:shipinhao_publish ok={ok}"

    if platform_key == "xiaohongshu":
        if any(
            k in blob
            for k in (
                "发布",
                "立即发布",
                "确认发布",
                "发布按钮",
                "点发布",
                "点击发布",
            )
        ):
            ok = await _xhs_click_publish(page, start_url=page.url)
            return f"fallback:xhs_publish ok={ok}"
        if any(k in blob for k in ("滚", "底部", "scroll")):
            try:
                await page.evaluate(
                    "window.scrollTo(0, document.body.scrollHeight)"
                )
            except Exception:
                pass
            await asyncio.sleep(0.5)
            return "fallback:xhs_scroll"

    return None


async def execute_action(
    page,
    action: dict[str, Any],
    *,
    video_path: Path | None,
    platform_key: str,
    cfg: AgentConfig,
    fields: dict[str, Any] | None = None,
) -> str:
    fields = fields or {}
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
            if platform_key == "xiaohongshu" and name == "click":
                ok = await _xhs_click_publish(page, start_url=page.url)
                return f"fallback:xhs_publish ok={ok}"
            fb = await _llm_action_fallback(
                page, action, platform_key=platform_key, fields=fields, cfg=cfg
            )
            if fb:
                return fb
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
        max_steps=browser_max_steps(platform_key),
        action_delay_min=_env_float("LLM_BROWSER_DELAY_MIN", 1.0),
        action_delay_max=_env_float("LLM_BROWSER_DELAY_MAX", 3.5),
        save_screenshots=_env("LLM_BROWSER_SAVE_SCREENSHOTS", "0").lower()
        in ("1", "true", "yes", "on"),
        use_deterministic=platform_use_deterministic(platform_key),
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

    if not cfg.use_deterministic and fields:
        if platform_key == "xiaohongshu":
            print(
                "  [agent] 小红书 LLM 逐步发布（禁止脚本填表，仅本地 file 上传除外）…",
                flush=True,
            )
        else:
            print(
                "  [agent] LLM 发布（脚本预填表单 + LLM 投稿/异常处理）…",
                flush=True,
            )

    if cfg.use_deterministic and fields:
        print("  [script] 尝试确定性填表+发布（零 LLM）…", flush=True)
        try:
            if await try_deterministic_publish(
                page, platform_key=platform_key, fields=fields, cfg=cfg
            ):
                for _ in range(5):
                    await asyncio.sleep(1)
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
                if platform_key == "xiaohongshu" and _xhs_url_published(page.url):
                    print("  [script] 检测到 published=true，发布完成", flush=True)
                    return {
                        "ok": True,
                        "steps": 0,
                        "llm_calls": 0,
                        "url": page.url,
                        "history": ["deterministic", "published_query"],
                    }
                if platform_key == "bilibili":
                    print(
                        "  [script] B站投稿未确认成功，转入 LLM 兜底…",
                        flush=True,
                    )
                else:
                    print("  [script] 已提交发布，退出", flush=True)
                    return {
                        "ok": True,
                        "steps": 0,
                        "llm_calls": 0,
                        "url": page.url,
                        "history": ["deterministic", "submitted_unverified"],
                    }
        except Exception as exc:
            print(f"  [script] 确定性步骤未完成: {exc}", flush=True)

    if platform_key == "xiaohongshu" and await _xhs_form_ready(page):
        llm_video_path = None
        print("  [script] 表单已就绪，LLM 兜底不再重复上传视频", flush=True)

    if platform_key == "bilibili" and await _bilibili_form_ready(page):
        llm_video_path = None
        print("  [script] B站表单已就绪，LLM 兜底不再重复上传视频", flush=True)

    bili_tags: list[str] = []
    if platform_key == "bilibili" and fields:
        bili_tags = _parse_bilibili_tags(str(fields.get("tags") or ""))

    llm_assist_platforms = ("bilibili", "douyin", "shipinhao", "xiaohongshu")
    if fields and platform_key in llm_assist_platforms:
        if platform_key == "xiaohongshu" or not cfg.use_deterministic:
            await _llm_assist_prefill(
                page, platform_key=platform_key, fields=fields, cfg=cfg
            )
            if platform_key != "xiaohongshu" and not cfg.use_deterministic:
                if await _llm_assist_try_publish(
                    page, platform_key=platform_key, fields=fields, cfg=cfg
                ):
                    history.append("script:预填后投稿")
                    hit = await _llm_publish_success(
                        page,
                        success_patterns=success_patterns,
                        start_url=start_url,
                        platform_key=platform_key,
                        history=history,
                        steps=0,
                        llm_calls=0,
                    )
                    if hit:
                        print("  [script] 预填+脚本投稿成功", flush=True)
                        return hit
                    print("  [agent] 脚本投稿未确认成功，LLM 接手…", flush=True)

    from llm_vision_client import llm_vision_available

    if not llm_vision_available():
        if platform_key == "xiaohongshu":
            raise LLMBrowserError(
                "小红书仅支持 LLM 逐步发布（不可走脚本兜底），请配置 AIHUBMIX_API_KEY 或 DASHSCOPE_API_KEY。"
            )
        raise LLMBrowserError(
            "确定性步骤未完成，且未配置 LLM API Key（AIHUBMIX_API_KEY 或 DASHSCOPE_API_KEY）。"
            "请检查 .env，或通过 scripts/publish-llm-browser.sh 启动以自动加载 .env。"
        )

    llm_label = "逐步发布" if platform_key == "xiaohongshu" else "投稿/异常"
    print(f"  [agent] 进入 LLM {llm_label}（最多 {cfg.max_steps} 步）…", flush=True)
    for step in range(1, cfg.max_steps + 1):
        await dismiss_overlays(page, platform_key=platform_key)

        if platform_key == "xiaohongshu":
            await xhs_prepare_page(page)
            await _xhs_disable_pk_cover(page)
        elif fields and platform_key in ("bilibili", "douyin", "shipinhao"):
            if await _llm_ready_to_publish(page, platform_key) and await _llm_assist_try_publish(
                page, platform_key=platform_key, fields=fields, cfg=cfg
            ):
                history.append(f"script:投稿 step{step}")
                hit = await _llm_publish_success(
                    page,
                    success_patterns=success_patterns,
                    start_url=start_url,
                    platform_key=platform_key,
                    history=history,
                    steps=step,
                    llm_calls=llm_calls,
                )
                if hit:
                    print(f"  [agent] 成功 step={step} url={hit['url']}", flush=True)
                    return hit
                continue

        if bili_tags and await _bilibili_try_autofill_tags(page, bili_tags, cfg):
            history.append("script:批量填写标签")
            state = await extract_page_state(page, screenshot_path=None)
            if _check_success(
                state, success_patterns, start_url=start_url, platform_key=platform_key
            ):
                print(f"  [agent] 成功 step={step} url={state.url}", flush=True)
                return {
                    "ok": True,
                    "steps": step,
                    "llm_calls": llm_calls,
                    "url": state.url,
                    "history": history,
                }
            continue

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
            system=_build_system_prompt(platform, platform_key=platform_key),
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
            page, action, video_path=llm_video_path, platform_key=platform_key, cfg=cfg,
            fields=fields,
        )
        history.append(summary)
        if summary.startswith("fallback:"):
            hit = await _llm_publish_success(
                page,
                success_patterns=success_patterns,
                start_url=start_url,
                platform_key=platform_key,
                history=history,
                steps=step,
                llm_calls=llm_calls,
            )
            if hit:
                print(f"  [agent] 脚本兜底成功 step={step} url={hit['url']}", flush=True)
                return hit
        await human_pause(cfg)

    raise LLMBrowserError(
        f"LLM {llm_label} {cfg.max_steps} 步仍未完成，已停止（请人工检查后台，勿立即重试）"
    )
