"""发布文案终端展示（供手动复制到各平台）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from douyin_caption import build_sau_fields
from paths import ROOT
from publish_resolve import load_script
from tiktok_caption import build_tiktok_fields
from youtube_caption import build_youtube_fields

# 国内视频：除 B 站外均需真人上传 (名称, 后台地址, 一句话操作提示)
VIDEO_MANUAL_PLATFORMS: list[tuple[str, str, str]] = [
    (
        "抖音",
        "https://creator.douyin.com/creator-micro/content/upload",
        "标题用上面「标题」，简介贴「简介」，话题加上「话题」（≤5 个）",
    ),
    (
        "小红书",
        "https://creator.xiaohongshu.com/publish/publish?from=homepage",
        "标题≤20 字带钩子，正文贴「简介」，行内加「话题」",
    ),
    (
        "视频号",
        "https://channels.weixin.qq.com/platform/post/create",
        "短标题（6–16 字），描述贴「简介」，加「话题」",
    ),
]

BILIBILI_VIDEO_UPLOAD_URL = (
    "https://member.bilibili.com/platform/upload/video/frame"
)
ZHIHU_COLUMN_URL = "https://zhuanlan.zhihu.com/write"


def _load_script_dict(script_path: Path) -> dict | None:
    if not script_path.is_file():
        return None
    try:
        data = json.loads(script_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    script = data.get("script", data)
    return script if isinstance(script, dict) else None


def build_publish_fields(script: dict | None) -> dict[str, str]:
    return build_sau_fields(script)


def print_manual_publish_pack(
    script_path: Path,
    video_path: Path | None = None,
    *,
    youtube_url: str = "",
    tiktok_url: str = "",
    bilibili_title: str = "",
    eastmoney_title: str = "",
    xueqiu_title: str = "",
    wechat_title: str = "",
    skip_auto_note: bool = False,
) -> None:
    script = _load_script_dict(script_path) or load_script(script_path)
    fields = build_publish_fields(script)
    yt_fields = build_youtube_fields(script)
    tk_fields = build_tiktok_fields(script)
    tags = fields.get("tags") or ""
    hashtags = " ".join(f"#{t.strip()}" for t in tags.split(",") if t.strip())
    yt_hashtags = " ".join(f"#{t}" for t in yt_fields.get("tags") or [])

    video_rel = ""
    forum_rel = ""
    has_forum = False
    if video_path:
        try:
            video_rel = str(video_path.resolve().relative_to(ROOT.resolve()))
        except ValueError:
            video_rel = str(video_path)
        forum_dir = video_path.parent / video_path.stem
        has_forum = forum_dir.is_dir() and (forum_dir / "post.md").is_file()
        if has_forum:
            try:
                forum_rel = str(forum_dir.resolve().relative_to(ROOT.resolve()))
            except ValueError:
                forum_rel = str(forum_dir)

    print("\n" + "═" * 58, flush=True)
    print("📋 发布文案（各平台通用，复制后按需微调）", flush=True)
    if video_rel:
        print(f"视频: {video_rel}", flush=True)
    if forum_rel:
        print(f"论坛图文: {forum_rel}/  （post.md + cover.jpg + cover_landscape.jpg）", flush=True)
    print("═" * 58, flush=True)

    print(f"\n标题: {fields['title']}", flush=True)
    desc_block = fields["desc"]
    if hashtags:
        desc_block = f"{desc_block}\n\n{hashtags}"
    print(f"\n简介+话题（整段复制）:\n{desc_block}", flush=True)

    print("\n【YouTube 自动发布】", flush=True)
    print(f"标题: {yt_fields['title']}", flush=True)
    print(f"标签: {', '.join(yt_fields.get('tags') or [])}", flush=True)
    if yt_hashtags:
        print(f"话题: {yt_hashtags}", flush=True)

    print("\n【TikTok · 复制到 App 发布页】", flush=True)
    print("（收件箱草稿不会自动带文案，请整段复制粘贴）", flush=True)
    print(tk_fields["title"], flush=True)

    _print_todo_checklist(
        script=script,
        video_rel=video_rel,
        forum_rel=forum_rel,
        has_forum=has_forum,
        youtube_url=youtube_url,
        tiktok_url=tiktok_url,
        bilibili_title=bilibili_title,
        eastmoney_title=eastmoney_title,
        xueqiu_title=xueqiu_title,
        wechat_title=wechat_title,
        skip_auto_note=skip_auto_note,
    )


def _read_bilibili_article_status() -> tuple[str, bool]:
    """从 logs/last_bilibili_publish.json 读取专栏 url 与是否已发布。"""
    log_path = ROOT / "logs" / "last_bilibili_publish.json"
    if not log_path.is_file():
        return "", False
    try:
        art = json.loads(log_path.read_text(encoding="utf-8")).get("article") or {}
    except (OSError, json.JSONDecodeError):
        return "", False
    return str(art.get("url") or ""), bool(art.get("published"))


def _print_todo_checklist(
    *,
    script: dict | None = None,
    video_rel: str,
    forum_rel: str,
    has_forum: bool,
    youtube_url: str,
    tiktok_url: str,
    bilibili_title: str,
    eastmoney_title: str,
    xueqiu_title: str,
    wechat_title: str,
    skip_auto_note: bool,
) -> None:
    """流程结束后的「待办清单」：国内视频 / 国内长文 / 国外视频（未开启则无项）。"""
    print("\n" + "═" * 58, flush=True)
    print("✅ 发布 TODO 清单（国内视频 · 国内长文 · 国外视频）", flush=True)
    print("═" * 58, flush=True)

    # 1) 国内视频：B 站 API 自动，其余真人上传
    src_hint = f"（成片 {video_rel}）" if video_rel else ""
    print(f"\n— 国内视频 · 真人上传 {src_hint}—", flush=True)
    print("  复制上面「标题 / 简介 / 话题」，按各平台习惯微调：", flush=True)
    if bilibili_enabled():
        if skip_auto_note:
            print("  [·] B站视频: 本次未执行自动发布", flush=True)
        elif bilibili_title:
            print(f"  [✓] B站视频: 已自动提交 — {bilibili_title}", flush=True)
        else:
            print(
                "  [!] B站视频: 未发布或失败 — ./scripts/publish-bilibili.sh"
                " 或先 ./bilibili-login.sh",
                flush=True,
            )
    else:
        print(f"  [ ] B站视频: {BILIBILI_VIDEO_UPLOAD_URL}", flush=True)
        print("        未开启：.env 设 AIVIDEO_PUBLISH_BILIBILI=1 并先 ./bilibili-login.sh", flush=True)
    for name, url, tip in VIDEO_MANUAL_PLATFORMS:
        print(f"  [ ] {name}: {url}", flush=True)
        print(f"        {tip}", flush=True)

    # 2) 国内长文：雪球 / 东财 / B 站专栏（Playwright 或 API）
    longform_on = (
        eastmoney_enabled()
        or xueqiu_enabled()
        or wechat_enabled()
        or (bilibili_enabled() and bilibili_article_enabled())
    )
    if longform_on:
        pack_hint = f"（{forum_rel}/post.md + cover.jpg）" if forum_rel else ""
        print(f"\n— 国内长文 · 自动发布 {pack_hint}—", flush=True)
        if skip_auto_note:
            print("  [·] 本次未执行自动发布（--no-publish / 预演）", flush=True)
        elif not has_forum and (eastmoney_enabled() or xueqiu_enabled()):
            print("  [!] 无论坛图文包，雪球/东财已跳过", flush=True)
        if eastmoney_enabled():
            if eastmoney_title:
                print(f"  [✓] 东方财富: {eastmoney_title}", flush=True)
            elif not skip_auto_note:
                print(
                    "  [!] 东方财富: 未发布或失败 — ./scripts/publish-eastmoney.sh",
                    flush=True,
                )
        if xueqiu_enabled():
            if xueqiu_title:
                print(f"  [✓] 雪球: {xueqiu_title}", flush=True)
            elif not skip_auto_note:
                print(
                    "  [!] 雪球: 未发布或失败 — ./scripts/publish-xueqiu.sh",
                    flush=True,
                )
        if bilibili_enabled() and bilibili_article_enabled():
            article_url, article_published = _read_bilibili_article_status()
            if bilibili_title and article_url:
                if article_published:
                    print(f"  [✓] B站专栏: 已发布 — {article_url}", flush=True)
                else:
                    print(
                        f"  [→] B站专栏: 草稿 — {article_url}"
                        "（创作中心确认后点发布）",
                        flush=True,
                    )
            elif not skip_auto_note and bilibili_title:
                print(
                    "  [·] B站专栏: 未同步（需论坛包 post.md + cover.jpg）",
                    flush=True,
                )
            elif not skip_auto_note:
                print("  [·] B站专栏: 随视频投稿一并处理", flush=True)
        if wechat_enabled():
            published = False
            log_path = ROOT / "logs" / "last_wechat_publish.json"
            if log_path.is_file():
                try:
                    payload = json.loads(log_path.read_text(encoding="utf-8"))
                    published = bool(payload.get("published"))
                except (OSError, json.JSONDecodeError):
                    pass
            if wechat_title and published:
                print(f"  [✓] 微信公众号: 已发表 — {wechat_title}", flush=True)
            elif wechat_title:
                print(f"  [→] 微信公众号: 草稿 — {wechat_title}", flush=True)
            elif not skip_auto_note:
                print(
                    "  [!] 微信公众号: 未发布或失败 — ./scripts/publish-wechat.sh"
                    " 或先 ./wechat-login.sh",
                    flush=True,
                )
        if has_forum:
            print(f"  [ ] 知乎专栏（仅手动）: {ZHIHU_COLUMN_URL}", flush=True)
            print("        post.md 第一行作标题，正文贴入并按【插入配图 N】上传配图", flush=True)

    # 3) 国外视频：仅当 .env 开启对应开关时出现待办
    if youtube_enabled() or tiktok_enabled():
        print("\n— 国外视频 · API 自动 —", flush=True)
        if skip_auto_note:
            print("  [·] 本次未执行自动发布（--no-publish / 预演）", flush=True)
        if youtube_enabled():
            if youtube_url:
                print(f"  [✓] YouTube: {youtube_url}", flush=True)
            elif not skip_auto_note:
                print(
                    "  [!] YouTube: 未发布或失败 — https://studio.youtube.com/",
                    flush=True,
                )
        if tiktok_enabled():
            if tiktok_url:
                print(f"  [✓] TikTok: {tiktok_url}", flush=True)
            elif not skip_auto_note:
                print("  [→] TikTok: 收件箱草稿 — 打开 App，", flush=True)
                print("      粘贴上面【TikTok】整段文案后点发布", flush=True)

    print("\n" + "─" * 58, flush=True)
    print("提示: 财经平台风控严，简介勿出现「荐股/收益/带单」等字眼。", flush=True)
    if script:
        try:
            from research import print_douyin_pre_publish_scan

            print_douyin_pre_publish_scan(script)
        except Exception:
            pass
    print("─" * 58 + "\n", flush=True)


def youtube_enabled() -> bool:
    value = os.environ.get("AIVIDEO_PUBLISH_YOUTUBE")
    if value is None or value.strip() == "":
        return True
    return value.strip().lower() in ("1", "true", "yes", "on")


def tiktok_enabled() -> bool:
    value = os.environ.get("AIVIDEO_PUBLISH_TIKTOK")
    if value is None or value.strip() == "":
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def eastmoney_enabled() -> bool:
    value = os.environ.get("AIVIDEO_PUBLISH_EASTMONEY")
    if value is None or value.strip() == "":
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def xueqiu_enabled() -> bool:
    value = os.environ.get("AIVIDEO_PUBLISH_XUEQIU")
    if value is None or value.strip() == "":
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def wechat_enabled() -> bool:
    value = os.environ.get("AIVIDEO_PUBLISH_WECHAT")
    if value is None or value.strip() == "":
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def bilibili_enabled() -> bool:
    value = os.environ.get("AIVIDEO_PUBLISH_BILIBILI")
    if value is None or value.strip() == "":
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def bilibili_article_enabled() -> bool:
    if not bilibili_enabled():
        return False
    value = os.environ.get("AIVIDEO_PUBLISH_BILIBILI_ARTICLE", "1")
    return value.strip().lower() not in ("0", "false", "no", "off")
