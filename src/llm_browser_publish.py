#!/usr/bin/env python3
"""基于大模型视觉 + Playwright 的抖音/视频号/小红书自适应发布。

与 vendor/social-auto-upload 固定选择器方案不同：
- 每步截图 + DOM 摘要 → 视觉模型决策
- 随机延迟 + 拟人打字，降低行为指纹一致性
- 复用现有 cookie（douyin_main.json / tencent_main.json）

用法:
  scripts/publish-llm-browser.sh douyin <video.mp4> [--archive-dir ...] [--probe] [--dry-run]
  scripts/publish-llm-browser.sh shipinhao <video.mp4> [--confirm]
  scripts/publish-llm-browser.sh xiaohongshu <video.mp4> [--confirm]
  scripts/publish-llm-browser.sh bilibili <video.mp4> [--confirm]
  scripts/publish-llm-browser.sh zhihu --forum-dir output/zh/xxx [--confirm]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from bilibili_caption import build_bilibili_fields
from douyin_caption import build_sau_fields, _strip_urls
from llm_browser_agent import AgentConfig, LLMBrowserError, human_pause, run_agent
from llm_vision_client import browser_max_steps, browser_model, browser_provider_label
from paths import ROOT
from publish_resolve import load_script, resolve_script_for_video
from sau_paths import chrome_executable, ensure_patchright_import
from social_caption import build_social_fields

PLATFORM_ALIASES = {
    "douyin": "douyin",
    "dy": "douyin",
    "shipinhao": "shipinhao",
    "tencent": "shipinhao",
    "channels": "shipinhao",
    "weixin": "shipinhao",
    "xiaohongshu": "xiaohongshu",
    "xhs": "xiaohongshu",
    "redbook": "xiaohongshu",
    "bilibili": "bilibili",
    "bili": "bilibili",
    "zhihu": "zhihu",
    "zh": "zhihu",
}

PLATFORM_LABEL = {
    "douyin": "抖音",
    "shipinhao": "视频号",
    "xiaohongshu": "小红书",
    "bilibili": "B站",
    "zhihu": "知乎专栏",
}

LOGIN_HINT = {
    "douyin": "./douyin-login.sh",
    "shipinhao": "./social-login.sh shipinhao",
    "xiaohongshu": "./social-login.sh xiaohongshu",
    "bilibili": "./bilibili-login.sh",
    "zhihu": "./scripts/login-cn.ps1 zhihu",
}

COOKIE_ENV = {
    "douyin": "SAU_DOUYIN_ACCOUNT",
    "shipinhao": "SAU_SHIPINHAO_ACCOUNT",
    "xiaohongshu": "SAU_XHS_ACCOUNT",
    "bilibili": "SAU_BILIBILI_ACCOUNT",
    "zhihu": "ZHIHU_ACCOUNT",
}

COOKIE_KEY = {
    "douyin": "douyin",
    "shipinhao": "tencent",
    "xiaohongshu": "xiaohongshu",
    "bilibili": "bilibili",
    "zhihu": "zhihu",
}


class PublishError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def sau_home() -> Path:
    custom = _env("SAU_HOME")
    if custom:
        return Path(custom).expanduser()
    return ROOT / "vendor" / "social-auto-upload"


def _chrome_path() -> str:
    return chrome_executable()


def _ensure_patchright() -> None:
    try:
        ensure_patchright_import(sau_home())
    except ImportError as exc:
        raise PublishError("未安装 patchright，请先运行: ./setup-sau.sh 或 .\\setup-windows.ps1") from exc


def cookie_path(platform: str, *, required: bool = True) -> Path | None:
    account = _env(COOKIE_ENV[platform], "main")
    path = sau_home() / "cookies" / f"{COOKIE_KEY[platform]}_{account}.json"
    if path.is_file():
        return path
    profile = profile_dir(platform)
    if profile.is_dir() and any(profile.iterdir()):
        return None
    if required:
        raise PublishError(
            f"未找到 cookie 或 Profile: {path}\n请先运行: {LOGIN_HINT[platform]}"
        )
    return None


def profile_dir(platform: str) -> Path:
    account = _env(COOKIE_ENV[platform], "main")
    # 与 social-login / shipinhao_login 共用 profile，避免每次冷启动丢登录态
    return sau_home() / "cookies" / "browser_profiles" / f"{COOKIE_KEY[platform]}_{account}"


def parse_archive_readme(readme: Path) -> dict[str, str]:
    text = readme.read_text(encoding="utf-8")
    out: dict[str, str] = {"title": "", "desc": "", "tags": ""}

    def _block(label: str) -> str:
        m = re.search(
            rf"\*\*{re.escape(label)}\*\*\s*\n\s*```\s*\n(.*?)\n```",
            text,
            re.S,
        )
        return m.group(1).strip() if m else ""

    out["title"] = _block("标题")
    out["tags"] = _block("标签")
    desc_block = _block("简介 + 话题") or _block("简介")
    out["desc"] = desc_block
    return out


def _tags_to_csv(raw) -> str:
    if isinstance(raw, list):
        return ",".join(str(t).strip() for t in raw if str(t).strip())
    return str(raw or "").strip()


def resolve_fields(
    video: Path,
    *,
    script_path: Path | None,
    archive_dir: Path | None,
    platform: str,
    title: str | None,
    desc: str | None,
    tags: str | None,
) -> dict:
    script = load_script(script_path)
    if script:
        if platform == "douyin":
            fields = build_sau_fields(script)
        elif platform == "bilibili":
            fields = build_bilibili_fields(script)
            return {
                "title": title or fields["title"],
                "desc": _strip_urls(desc or fields["desc"]),
                "tags": tags or fields["tags"],
                "tid": fields.get("tid", 207),
            }
        else:
            social_key = "xiaohongshu" if platform == "xiaohongshu" else "tencent"
            fields = build_social_fields(script, social_key)
        return {
            "title": title or fields["title"],
            "desc": _strip_urls(desc or fields["desc"]),
            "tags": tags or _tags_to_csv(fields.get("tags")),
            "short_title": fields.get("short_title") or "",
        }

    if archive_dir:
        readme = archive_dir / "README.md"
        if readme.is_file():
            parsed = parse_archive_readme(readme)
            return {
                "title": title or parsed["title"] or video.stem,
                "desc": _strip_urls(desc or parsed["desc"]),
                "tags": tags or parsed["tags"],
                "short_title": (title or parsed["title"] or video.stem)[:16],
            }

    return {
        "title": title or video.stem,
        "desc": _strip_urls(desc or ""),
        "tags": tags or "",
        "short_title": (title or video.stem)[:16],
    }


def pick_random_archive_video(locale: str = "zh") -> tuple[Path, Path | None]:
    base = ROOT / "archive" / "published"
    if not base.is_dir():
        raise PublishError("archive/published 不存在")
    dirs = sorted(base.iterdir(), reverse=True)
    candidates: list[tuple[Path, Path | None]] = []
    for day in dirs:
        loc_dir = day / locale
        if not loc_dir.is_dir():
            continue
        for mp4 in loc_dir.glob("*.mp4"):
            stem = mp4.stem
            pack = loc_dir / stem
            candidates.append((mp4, pack if pack.is_dir() else None))
    if not candidates:
        raise PublishError(f"archive 下没有 {locale} 视频")
    return random.choice(candidates)


def resolve_zhihu_fields(forum_dir: Path) -> dict:
    from publish_caption import zhihu_auto_publish
    from zhihu_publisher import parse_forum_pack

    data = parse_forum_pack(forum_dir)
    body_bits = [str(s.get("text") or "").strip() for s in data.get("sections") or []]
    body = "\n\n".join(x for x in body_bits if x)
    return {
        "title": data["title"],
        "desc": body[:4000],
        "tags": "",
        "forum_dir": str(forum_dir.resolve()),
        "auto_publish": zhihu_auto_publish(),
    }


def build_task(platform: str, fields: dict) -> str:
    tags = [t.strip().lstrip("#") for t in str(fields.get("tags") or "").split(",") if t.strip()]
    tag_line = " ".join(f"#{t}" for t in tags[:5])
    if platform == "douyin":
        return f"""在抖音创作者平台上传并发布一条短视频：
1. 上传本地 MP4（若尚未上传）
2. 标题（≤30字）: {fields['title']}
3. 作品描述/简介: {fields['desc']}
4. 添加话题（最多5个）: {tag_line or '（无额外话题）'}
5. 使用默认首帧封面，不要折腾自定义封面
6. 不要主动勾选 AI 内容声明
7. 等视频上传完成后点击「发布」
8. 成功标志：跳转到内容管理页或出现发布成功提示"""
    if platform == "shipinhao":
        body = f"{fields['desc']} {tag_line}".strip()
        return f"""在微信视频号创作者平台上传并发布一条短视频：
1. 上传本地 MP4（若尚未上传）
2. 等视频文件传完即可，**不要**上传/编辑封面，**不要**等待封面预览加载
3. 若有封面相关弹窗，点「取消/关闭/跳过」关掉
4. 在描述区填写: {body}
5. 勾选「视频为原创」并完成原创声明
6. 不要填写短标题
7. 直接点击「发表」
8. 成功标志：跳转到 platform/post/list 或出现发表成功"""
    if platform == "xiaohongshu":
        return f"""在小红书创作者平台发布一条短视频：
1. 上传本地 MP4（视频发布页，不是图文）
2. 等视频传完即可，**不要**编辑/等待/设置封面；若有封面弹窗点「取消/关闭」或 Escape 关掉
3. 标题（≤20字）: {fields['title']}
4. 正文描述: {fields['desc']}
5. 话题（行内 #，最多5个）: {tag_line or '（无）'}
6. **不要**开启「PK封面」；**不要**编辑/等待封面，使用默认主封面即可
7. 点击左下角「设置」→ 开启「声明原创」（勿在封面区域点任何开关）
8. 点击「发布」或「立即发布」，若有二次确认弹窗点「确认发布」
9. 成功标志：note-manage 或页面出现「发布成功」（非带 __debugger__ 的假跳转）"""
    if platform == "bilibili":
        tags = [t.strip().lstrip("#") for t in str(fields.get("tags") or "").split(",") if t.strip()]
        tag_line = ",".join(tags[:12])
        tid = fields.get("tid", 207)
        return f"""在 B 站创作中心上传并投稿一条视频：
1. **必须等视频上传 100% 完成**后再操作（进度条到 100% 或显示上传完成）
2. 若标题/简介/标签已填好，**不要重复 type 修改**，只做：选分区 → 点投稿
3. 标题（≤80字）: {fields['title']}
4. 简介: {fields['desc']}
5. 标签（最多12个）: {tag_line or '（无）'}；删除「生活记录/记录/vlog」等无关默认标签
6. 分区 tid={tid}：在分区下拉选「财经杂谈」或最接近的财经类（勿留 vlog）
7. 使用默认封面
8. 点击「立即投稿」，若有确认弹窗点「确认投稿」
9. 只有页面明确出现扫码/滑块验证时才 need_human；勿因分区难选就 need_human
10. 成功：跳转到稿件管理或出现投稿成功提示"""
    if platform == "zhihu":
        publish_step = (
            "填写完成后点击「发布」"
            if fields.get("auto_publish")
            else "填写完成后点击「保存草稿」"
        )
        return f"""在知乎专栏写作页发布长文（论坛图文包）：
1. 打开写作页，标题: {fields['title']}
2. 论坛包目录: {fields.get('forum_dir')}（含 post.md、cover.jpg、images/）
3. 正文需按 post.md 分段填入，并在对应位置插入 images/ 配图
4. 正文摘要（供参考）:\n{fields.get('desc', '')[:1200]}
5. {publish_step}
6. 成功：草稿箱出现该标题，或发布成功页"""
    raise PublishError(f"未知平台 task: {platform}")


def platform_url(platform: str) -> str:
    urls = {
        "douyin": "https://creator.douyin.com/creator-micro/content/upload",
        "shipinhao": "https://channels.weixin.qq.com/platform/post/create",
        "xiaohongshu": "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=video",
        "bilibili": "https://member.bilibili.com/platform/upload/video/frame",
        "zhihu": "https://zhuanlan.zhihu.com/write",
    }
    return urls[platform]


def success_patterns(platform: str) -> list[str]:
    patterns = {
        "douyin": ["content/manage", "发布成功", "已发布", "作品管理"],
        "shipinhao": ["platform/post/list", "发表成功", "已发表", "内容管理"],
        "xiaohongshu": [
            "note-manage",
            "content-manager",
            "发布成功",
            "笔记管理",
        ],
        "bilibili": [
            "platform/upload-manager",
            "upload-manager",
            "投稿成功",
            "稿件管理",
        ],
        "zhihu": [
            "creator/manage",
            "drafts",
            "发布成功",
            "zhuanlan.zhihu.com/p/",
        ],
    }
    return patterns[platform]


async def _goto_page(page, url: str, *, timeout_ms: int = 90_000) -> None:
    last_exc = None
    for wait_until in ("commit", "domcontentloaded"):
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            if any(
                d in (page.url or "")
                for d in (
                    "creator.douyin.com",
                    "channels.weixin.qq.com",
                    "creator.xiaohongshu.com",
                    "member.bilibili.com",
                    "zhuanlan.zhihu.com",
                )
            ):
                return
            return
        except Exception as exc:
            last_exc = exc
            url = page.url or ""
            if any(
                d in url
                for d in (
                    "creator.douyin.com",
                    "channels.weixin.qq.com",
                    "creator.xiaohongshu.com",
                    "member.bilibili.com",
                    "zhuanlan.zhihu.com",
                )
            ):
                return
    if last_exc:
        raise last_exc


async def _launch_context(p, platform: str, *, headed: bool):
    launch: dict = {
        "headless": not headed,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--lang=zh-CN",
            "--no-first-run",
            "--window-size=1440,900",
            "--disable-remote-fonts",
        ],
    }
    chrome = _chrome_path()
    if chrome:
        launch["executable_path"] = chrome
    else:
        launch["channel"] = "chrome"

    profile = profile_dir(platform)
    cookie = cookie_path(platform, required=False)
    use_profile = _env("LLM_BROWSER_USE_PROFILE", "1").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    if platform == "xiaohongshu":
        launch["args"].append("--disable-geolocation")

    if use_profile and profile.is_dir() and any(profile.iterdir()):
        ctx_kw: dict = {
            "locale": "zh-CN",
            "timezone_id": "Asia/Shanghai",
            "viewport": {"width": 1440, "height": 900},
        }
        context = await p.chromium.launch_persistent_context(
            str(profile),
            **ctx_kw,
            **launch,
        )
        return context, None

    if not cookie:
        raise PublishError(
            f"未找到登录态（cookie 或 Profile）\n请先运行: {LOGIN_HINT[platform]}"
        )

    browser = await p.chromium.launch(**launch)
    context = await browser.new_context(
        storage_state=str(cookie),
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
        viewport={"width": 1440, "height": 900},
    )
    try:
        home = str(sau_home())
        if home not in sys.path:
            sys.path.insert(0, home)
        from utils.base_social_media import set_init_script

        context = await set_init_script(context)
    except Exception:
        pass
    return context, browser


async def probe_page(platform: str, *, headed: bool = True) -> dict:
    _ensure_patchright()
    from patchright.async_api import async_playwright
    from llm_browser_agent import extract_page_state

    async with async_playwright() as p:
        context, browser = await _launch_context(p, platform, headed=headed)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await _goto_page(page, platform_url(platform))
            await human_pause(AgentConfig())
            shot = ROOT / "logs" / "llm_browser" / f"{platform}_probe.png"
            state = await extract_page_state(page, screenshot_path=shot)
            login_markers = ("扫码登录", "手机号登录", "请登录", "login", "APP扫一扫")
            logged_in = not any(m in state.body_snippet for m in login_markers)
            if platform == "douyin":
                try:
                    from douyin_session import verify_upload_page

                    cookie_ok = await verify_upload_page(root=ROOT, use_profile=True)
                except Exception:
                    cookie_ok = False
                logged_in = logged_in and cookie_ok
            elif platform == "shipinhao":
                try:
                    from shipinhao_session import verify_upload_page

                    cookie_ok = await verify_upload_page(root=ROOT, use_profile=True)
                except Exception:
                    cookie_ok = False
                logged_in = logged_in and cookie_ok
            else:
                cookie_ok = logged_in
            return {
                "platform": platform,
                "url": state.url,
                "logged_in": logged_in,
                "cookie_valid": cookie_ok,
                "screenshot": str(shot),
                "body_preview": state.body_snippet[:200],
            }
        finally:
            if browser:
                await browser.close()
            else:
                await context.close()


async def publish_zhihu_async(
    forum_dir: Path,
    fields: dict,
    *,
    headed: bool,
) -> dict:
    """知乎：优先确定性填表（含配图），失败再走 LLM 视觉兜底。"""
    draft_only = not fields.get("auto_publish")
    try:
        from zhihu_publisher import publish_forum_pack

        result = await publish_forum_pack(
            forum_dir,
            headless=not headed,
            draft_only=draft_only,
        )
        print("  [script] 知乎专栏发布完成（确定性）", flush=True)
        return {
            "ok": True,
            "steps": 0,
            "llm_calls": 0,
            "url": result.get("url") or "",
            "history": ["deterministic"],
            "published": bool(result.get("published")),
            "draft_only": draft_only,
            "title": result.get("title") or fields.get("title"),
        }
    except Exception as exc:
        print(f"  [script] 知乎确定性失败，进入 LLM 兜底: {exc}", flush=True)

    _ensure_patchright()
    from patchright.async_api import async_playwright

    task = build_task("zhihu", fields)
    async with async_playwright() as p:
        context, browser = await _launch_context(p, "zhihu", headed=headed)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            print(f"  打开 {platform_url('zhihu')} …", flush=True)
            await _goto_page(page, platform_url("zhihu"))
            await asyncio.sleep(2)
            await human_pause(AgentConfig())
            result = await run_agent(
                page,
                platform=PLATFORM_LABEL["zhihu"],
                platform_key="zhihu",
                task=task,
                fields=fields,
                video_path=None,
                success_patterns=success_patterns("zhihu"),
                pre_upload=False,
            )
            cookie = cookie_path("zhihu", required=False)
            if cookie:
                try:
                    await context.storage_state(path=str(cookie))
                    print(f"  cookie 已更新: {cookie}", flush=True)
                except Exception:
                    pass
            result["forum_dir"] = str(forum_dir)
            result["title"] = fields.get("title")
            result["published"] = bool(fields.get("auto_publish"))
            result["draft_only"] = not fields.get("auto_publish")
            return result
        finally:
            if browser:
                await browser.close()
            else:
                await context.close()


async def publish_async(
    platform: str,
    video: Path | None,
    fields: dict,
    *,
    headed: bool,
    probe_only: bool,
    forum_dir: Path | None = None,
) -> dict:
    if platform == "zhihu":
        if not forum_dir:
            raise PublishError("知乎发布需要 --forum-dir")
        if probe_only:
            return await probe_page(platform, headed=headed)
        return await publish_zhihu_async(forum_dir, fields, headed=headed)

    if not video:
        raise PublishError(f"{platform} 发布需要视频路径")
    _ensure_patchright()
    from patchright.async_api import async_playwright

    if probe_only:
        return await probe_page(platform, headed=headed)

    task = build_task(platform, fields)
    async with async_playwright() as p:
        context, browser = await _launch_context(p, platform, headed=headed)
        page = None
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            print(f"  打开 {platform_url(platform)} …", flush=True)
            await _goto_page(page, platform_url(platform))
            await asyncio.sleep(2)
            await human_pause(AgentConfig())

            result = await run_agent(
                page,
                platform=PLATFORM_LABEL.get(platform, platform),
                platform_key=platform,
                task=task,
                fields=fields,
                video_path=video,
                success_patterns=success_patterns(platform),
                pre_upload=True,
            )

            cookie = cookie_path(platform, required=False)
            if cookie:
                try:
                    await context.storage_state(path=str(cookie))
                    print(f"  cookie 已更新: {cookie}", flush=True)
                except Exception:
                    pass
            result["video"] = str(video)
            result["platform"] = platform
            result["title"] = fields["title"]
            return result
        finally:
            if browser:
                await browser.close()
            else:
                await context.close()


def resolve_playwright_python() -> Path | None:
    venv_py = sau_home() / ".venv" / "bin" / "python3"
    if venv_py.is_file():
        return venv_py
    venv_py_win = sau_home() / ".venv" / "Scripts" / "python.exe"
    if venv_py_win.is_file():
        return venv_py_win
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="大模型视觉浏览器发布（抖音/视频号/小红书/B站/知乎）"
    )
    parser.add_argument(
        "platform",
        help="douyin | shipinhao | xiaohongshu | bilibili | zhihu",
    )
    parser.add_argument("video", nargs="?", help="MP4 路径（知乎可省略）")
    parser.add_argument("--forum-dir", help="论坛图文包目录（知乎必填）")
    parser.add_argument("--script", help="脚本 JSON")
    parser.add_argument("--archive-dir", help="归档素材目录（含 README.md）")
    parser.add_argument("--title", help="覆盖标题")
    parser.add_argument("--desc", help="覆盖简介")
    parser.add_argument("--tags", help="覆盖标签（逗号分隔）")
    parser.add_argument("--random", action="store_true", help="从 archive 随机选视频")
    parser.add_argument("--probe", action="store_true", help="只探测登录态+截图，不发布")
    parser.add_argument("--dry-run", action="store_true", help="只打印参数")
    parser.add_argument("--confirm", action="store_true", help="确认实际发布（默认仅 probe 时安全）")
    parser.add_argument("--headed", action="store_true", default=True, help="有头 Chrome（默认）")
    parser.add_argument("--headless", action="store_true", help="无头（不推荐，易风控）")
    args = parser.parse_args()

    venv_py = resolve_playwright_python()
    if venv_py and Path(sys.executable).resolve() != venv_py.resolve() and not os.environ.get(
        "AIVIDEO_PUBLISH_REEXEC"
    ):
        os.environ["AIVIDEO_PUBLISH_REEXEC"] = "1"
        os.execv(str(venv_py), [str(venv_py), *sys.argv])

    platform = PLATFORM_ALIASES.get(args.platform.strip().lower())
    if not platform:
        raise SystemExit(f"未知平台: {args.platform}")

    forum_dir = Path(args.forum_dir).resolve() if args.forum_dir else None
    if platform == "zhihu":
        if not forum_dir or not forum_dir.is_dir():
            raise SystemExit("知乎发布需要 --forum-dir（含 post.md 的论坛包目录）")
        fields = resolve_zhihu_fields(forum_dir)
        video = None
        archive_dir = None
        script_path = None
    else:
        archive_dir = Path(args.archive_dir).resolve() if args.archive_dir else None
        if args.random or not args.video:
            video, auto_dir = pick_random_archive_video("zh")
            archive_dir = archive_dir or auto_dir
            print(f"随机选中: {video}", flush=True)
        else:
            video = Path(args.video).resolve()
            if not video.is_file():
                raise SystemExit(f"视频不存在: {video}")
            if not archive_dir:
                sibling = video.parent / video.stem
                if sibling.is_dir():
                    archive_dir = sibling
                else:
                    for loc in ("zh", "en"):
                        loc_pack = video.parent / loc / video.stem
                        if loc_pack.is_dir():
                            archive_dir = loc_pack
                            break

        script_path = resolve_script_for_video(video, args.script)
        fields = resolve_fields(
            video,
            script_path=script_path,
            archive_dir=archive_dir,
            platform=platform,
            title=args.title,
            desc=args.desc,
            tags=args.tags,
        )

    print(f"平台: {PLATFORM_LABEL[platform]}（LLM 视觉代理）")
    if video:
        print(f"视频: {video}")
    if forum_dir:
        print(f"论坛包: {forum_dir}")
    print(f"标题: {fields['title']}")
    print(f"简介: {fields['desc'][:120]}{'…' if len(fields['desc']) > 120 else ''}")
    if fields.get("tags"):
        print(f"标签: {fields['tags']}")
    if platform == "shipinhao":
        print(f"短标题: {fields.get('short_title')}")

    print(
        f"模型: {browser_model()}（{browser_provider_label()} · 确定性优先，"
        f"LLM 最多 {browser_max_steps()} 步）"
    )
    ck = cookie_path(platform, required=False)
    prof = profile_dir(platform)
    if ck:
        print(f"Cookie: {ck}")
    if prof.is_dir() and any(prof.iterdir()):
        print(f"Profile: {prof}（持久化登录，推荐）")
    elif not ck:
        print(
            f"登录态: 未找到\n  请先运行: {LOGIN_HINT.get(platform, './social-login.sh')}",
            flush=True,
        )

    if args.dry_run:
        print("（dry-run）")
        return 0

    headed = not args.headless
    do_publish = args.confirm and not args.probe

    if args.probe or not do_publish:
        print("\n=== 探测模式（不发布）===", flush=True)
        print("实际发布请加: --confirm", flush=True)
        try:
            info = asyncio.run(probe_page(platform, headed=headed))
            print(json.dumps(info, ensure_ascii=False, indent=2))
            if not info.get("logged_in"):
                hint = LOGIN_HINT.get(platform, "./social-login.sh")
                print(f"\n⚠️ 登录态无效，请先运行: {hint}", file=sys.stderr)
                return 1
            print("\n✅ 登录态探测通过", flush=True)
            return 0
        except (PublishError, LLMBrowserError) as exc:
            print(str(exc), file=sys.stderr)
            return 1

    print("\n=== 开始 LLM 视觉发布 ===", flush=True)
    try:
        result = asyncio.run(
            publish_async(
                platform,
                video,
                fields,
                headed=headed,
                probe_only=False,
                forum_dir=forum_dir,
            )
        )
        log_path = ROOT / "logs" / f"last_llm_{platform}_publish.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            **result,
            "method": "llm_browser",
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if platform == "zhihu":
            zh_log = {
                "at": payload["published_at"],
                "title": payload.get("title") or fields.get("title"),
                "pack_dir": str(forum_dir) if forum_dir else "",
                "published": bool(payload.get("published")),
                "draft_only": bool(payload.get("draft_only")),
                "url": payload.get("url") or "",
                "method": "llm_browser",
            }
            (ROOT / "logs" / "last_zhihu_publish.json").write_text(
                json.dumps(zh_log, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if platform == "bilibili":
            bili_log = {
                "at": payload["published_at"],
                "title": payload.get("title") or fields.get("title"),
                "video": str(video) if video else "",
                "method": "llm_browser",
                "ok": payload.get("ok"),
            }
            (ROOT / "logs" / "last_bilibili_publish.json").write_text(
                json.dumps(bili_log, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if not result.get("ok"):
            print(
                f"\n❌ 发布未确认成功（url={result.get('url', '')}）",
                file=sys.stderr,
            )
            print(f"  记录: {log_path}", file=sys.stderr)
            print("  请到平台后台确认；若未出现新作品，请重登后再试。", file=sys.stderr)
            return 1
        print(f"\n✅ 发布流程完成（请在平台后台确认审核状态）")
        print(f"  记录: {log_path}")
        return 0
    except (PublishError, LLMBrowserError) as exc:
        print(f"\n❌ {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
