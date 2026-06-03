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

# 需要手动上传「视频」的国内平台：(名称, 后台地址, 一句话操作提示)
VIDEO_MANUAL_PLATFORMS: list[tuple[str, str, str]] = [
    (
        "抖音",
        "https://creator.douyin.com/creator-micro/content/upload",
        "标题用上面「标题」，简介贴「简介」，话题加上「话题」（≤5 个）",
    ),
    (
        "B站",
        "https://member.bilibili.com/platform/upload/video/frame",
        "标题/简介/标签见上方；分区默认「知识·财经商业」(tid=207)",
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

# 需要手动发「图文」的财经论坛：(名称, 发布地址)
FORUM_MANUAL_PLATFORMS: list[tuple[str, str]] = [
    ("雪球", "https://mp.xueqiu.com/"),
    ("东方财富(股吧/财富号)", "https://mpservice.eastmoney.com/"),
]


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
        skip_auto_note=skip_auto_note,
    )


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
    skip_auto_note: bool,
) -> None:
    """流程结束后的「待办清单」，提醒哪些需要手动发，免得忘。"""
    print("\n" + "═" * 58, flush=True)
    print("✅ 发布 TODO 清单（按顺序操作；国内平台务必真人上传，勿用脚本）", flush=True)
    print("═" * 58, flush=True)

    # 1) 自动发布平台状态
    print("\n— 自动发布（API，无需手动）—", flush=True)
    if skip_auto_note and not youtube_url and not tiktok_url:
        print("  · 本次跳过自动发布（--no-publish / 预演）", flush=True)
    else:
        if youtube_url:
            print(f"  [✓] YouTube 已发布: {youtube_url}", flush=True)
        else:
            print("  [!] YouTube 未发布或失败，必要时手动补发: https://studio.youtube.com/", flush=True)
        if tiktok_url:
            print(f"  [✓] TikTok 已发布: {tiktok_url}", flush=True)
        else:
            print("  [→] TikTok 已上传到收件箱草稿 —— 打开 App，", flush=True)
            print("      粘贴上面【TikTok】整段文案后点发布", flush=True)
        if eastmoney_enabled():
            if eastmoney_title:
                print(f"  [✓] 东方财富已提交: {eastmoney_title}", flush=True)
            else:
                print("  [!] 东方财富未发布或失败，可手动: ./scripts/publish-eastmoney.sh", flush=True)
        if xueqiu_enabled():
            if xueqiu_title:
                print(f"  [✓] 雪球已提交: {xueqiu_title}", flush=True)
            else:
                print("  [!] 雪球未发布或失败，可手动: ./scripts/publish-xueqiu.sh", flush=True)
        if bilibili_enabled():
            if bilibili_title:
                print(f"  [✓] B站已提交: {bilibili_title}", flush=True)
            else:
                print(
                    "  [!] B站未发布或失败，可手动: ./scripts/publish-bilibili.sh"
                    " 或先 ./bilibili-login.sh",
                    flush=True,
                )

    # 2) 手动发布视频（抖音/小红书/视频号；B站已自动则跳过）
    src_hint = f"（上传成片 {video_rel}）" if video_rel else ""
    print(f"\n— 手动发布·视频 {src_hint}—", flush=True)
    print("  复制上面「标题 / 简介 / 话题」，按各平台习惯微调：", flush=True)
    for name, url, tip in VIDEO_MANUAL_PLATFORMS:
        if name == "B站" and bilibili_enabled() and bilibili_title:
            print(f"  [✓] {name}: 已自动发布", flush=True)
            continue
        if name == "B站" and not bilibili_enabled():
            print(f"  [ ] {name}: {url}", flush=True)
            print("        未开启自动发布：.env 设 AIVIDEO_PUBLISH_BILIBILI=1 并先 ./bilibili-login.sh", flush=True)
            continue
        print(f"  [ ] {name}: {url}", flush=True)
        print(f"        {tip}", flush=True)

    # 3) 手动发布图文（论坛包；东财/雪球已自动则跳过）
    if has_forum:
        auto_forum = (eastmoney_enabled() and eastmoney_title) or (
            xueqiu_enabled() and xueqiu_title
        )
        print(f"\n— 手动发布·图文（用 {forum_rel}/post.md + cover.jpg）—", flush=True)
        if auto_forum:
            print("  论坛图文已由 Playwright 自动提交，剩余平台（如有）：", flush=True)
        else:
            print("  post.md 第一行做标题，正文整段贴入，按【插入配图 N】上传 images/0N.jpg：", flush=True)
        for name, url in FORUM_MANUAL_PLATFORMS:
            if name.startswith("东方财富") and eastmoney_enabled() and eastmoney_title:
                print(f"  [✓] {name}: 已自动发布", flush=True)
                continue
            if name == "雪球" and xueqiu_enabled() and xueqiu_title:
                print(f"  [✓] {name}: 已自动发布", flush=True)
                continue
            print(f"  [ ] {name}: {url}", flush=True)
        if not xueqiu_enabled():
            print("        雪球首页推荐位可改用 cover_landscape.jpg（16:9 横图）", flush=True)

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


def bilibili_enabled() -> bool:
    value = os.environ.get("AIVIDEO_PUBLISH_BILIBILI")
    if value is None or value.strip() == "":
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")
