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

# ① 完全手动 · 视频（名称, 后台地址, 操作提示）
VIDEO_MANUAL_PLATFORMS: list[tuple[str, str, str]] = [
    (
        "抖音",
        "https://creator.douyin.com/creator-micro/content/upload",
        "标题用上面「标题」，简介贴「简介」，话题加上「话题」（≤5 个）",
    ),
    (
        "小红书视频",
        "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=video",
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
ZHIHU_DRAFTS_URL = "https://zhuanlan.zhihu.com/creator/manage/drafts"
# 图文草稿在发布页「草稿箱」（浏览器本地），不是笔记管理里的云端草稿
XHS_DRAFTS_URL = (
    "https://creator.xiaohongshu.com/publish/publish?target=image"
)
XHS_OPEN_CREATOR_CMD = "./xhs-open-creator.sh"
XHS_MANUAL_PUBLISH_TIP = (
    "草稿箱 → 图文笔记 → 编辑 → 左侧滚到底 → 点红色「发布」"
)
WECHAT_DRAFTS_URL = "https://mp.weixin.qq.com/cgi-bin/appmsg?begin=0&count=10&type=77&action=list_card"


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
    zhihu_title: str = "",
    xhs_article_title: str = "",
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

    todo_kwargs = dict(
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
        zhihu_title=zhihu_title,
        xhs_article_title=xhs_article_title,
        skip_auto_note=skip_auto_note,
    )
    _print_todo_checklist(**todo_kwargs)
    if video_path and has_forum:
        sync_todo_to_forum_readme(video_path, **todo_kwargs)


def _read_last_log_bool(log_name: str, key: str) -> bool:
    log_path = ROOT / "logs" / log_name
    if not log_path.is_file():
        return False
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(data.get(key))


def _read_last_log_field(log_name: str, *keys: str) -> str:
    log_path = ROOT / "logs" / log_name
    if not log_path.is_file():
        return ""
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    cur: object = data
    for key in keys:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(key)
    return str(cur or "").strip()


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


README_TODO_HEADING = "## 发布 TODO 清单"


def _todo_config_summary() -> str:
    """当前 .env 中与待办相关的开关摘要。"""
    bits: list[str] = []
    if bilibili_enabled():
        bits.append("B站=开")
        if bilibili_skip_video():
            bits.append("B站视频=手动(BILIBILI_SKIP_VIDEO)")
        else:
            bits.append("B站视频=自动")
        if bilibili_article_enabled():
            if bilibili_article_auto_publish():
                bits.append("B站专栏=自动点发布")
            else:
                bits.append("B站专栏=草稿+手动发布")
    if eastmoney_enabled():
        bits.append("东财=自动")
    if xueqiu_enabled():
        bits.append("雪球=自动")
    if wechat_enabled():
        bits.append("公众号=草稿+手动发表")
    if zhihu_enabled():
        bits.append("知乎专栏=草稿+手动发布")
    if xhs_article_enabled():
        bits.append("小红书图文=草稿+手动发布")
    if youtube_enabled():
        bits.append("YouTube=自动")
    if tiktok_enabled():
        bits.append("TikTok=收件箱+手动")
    return " · ".join(bits) if bits else "（未开启 API 发布渠道）"


def _append_todo_items(
    items: list[tuple[str, str, list[str]]],
    *,
    mark: str,
    headline: str,
    sublines: list[str] | None = None,
) -> None:
    items.append((mark, headline, sublines or []))


def build_todo_checklist_items(
    *,
    video_rel: str,
    forum_rel: str,
    has_forum: bool,
    youtube_url: str,
    tiktok_url: str,
    bilibili_title: str,
    eastmoney_title: str,
    xueqiu_title: str,
    wechat_title: str,
    zhihu_title: str,
    xhs_article_title: str,
    skip_auto_note: bool,
) -> list[tuple[str, list[tuple[str, str, list[str]]]]]:
    """按 .env 与本次发布结果生成待办分组：(分组标题, [(标记, 主行, 子行), ...])。"""
    sections: list[tuple[str, list[tuple[str, str, list[str]]]]] = []

    manual_video: list[tuple[str, str, list[str]]] = []
    for name, url, tip in VIDEO_MANUAL_PLATFORMS:
        _append_todo_items(
            manual_video,
            mark=" ",
            headline=f"{name}: {url}",
            sublines=[tip],
        )
    if bilibili_enabled() and bilibili_skip_video():
        _append_todo_items(
            manual_video,
            mark=" ",
            headline=f"B站视频: {BILIBILI_VIDEO_UPLOAD_URL}",
            sublines=[".env 已设 BILIBILI_SKIP_VIDEO=1，请手动上传成片"],
        )
    if manual_video:
        src = f"（成片 {video_rel}）" if video_rel else ""
        sections.append((f"① 完全手动 · 视频 {src}".strip(), manual_video))

    auto_items: list[tuple[str, str, list[str]]] = []
    auto_on = (
        (bilibili_enabled() and not bilibili_skip_video())
        or eastmoney_enabled()
        or xueqiu_enabled()
        or youtube_enabled()
    )
    if auto_on:
        if skip_auto_note:
            _append_todo_items(
                auto_items,
                mark="·",
                headline="本次未执行自动发布（--no-publish / 预演）",
            )
        if bilibili_enabled() and not bilibili_skip_video():
            if bilibili_title and not skip_auto_note:
                _append_todo_items(
                    auto_items, mark="✓", headline=f"B站视频: {bilibili_title}"
                )
            elif not skip_auto_note:
                _append_todo_items(
                    auto_items,
                    mark="!",
                    headline="B站视频: 失败 — ./scripts/publish-bilibili.sh",
                )
            else:
                _append_todo_items(
                    auto_items,
                    mark=" ",
                    headline="B站视频: 将走 biliup 自动投稿（本次未跑）",
                )
        if eastmoney_enabled():
            if eastmoney_title and not skip_auto_note:
                _append_todo_items(
                    auto_items, mark="✓", headline=f"东方财富: {eastmoney_title}"
                )
            elif not skip_auto_note:
                _append_todo_items(
                    auto_items,
                    mark="!",
                    headline="东方财富: 失败 — ./scripts/publish-eastmoney.sh",
                )
            elif skip_auto_note:
                _append_todo_items(
                    auto_items,
                    mark=" ",
                    headline="东方财富: 将自动提交（本次未跑）",
                )
        if xueqiu_enabled():
            if xueqiu_title and not skip_auto_note:
                _append_todo_items(auto_items, mark="✓", headline=f"雪球: {xueqiu_title}")
            elif not skip_auto_note:
                _append_todo_items(
                    auto_items, mark="!", headline="雪球: 失败 — ./scripts/publish-xueqiu.sh"
                )
            elif skip_auto_note:
                _append_todo_items(
                    auto_items, mark=" ", headline="雪球: 将自动提交（本次未跑）"
                )
        if youtube_enabled():
            if youtube_url and not skip_auto_note:
                _append_todo_items(auto_items, mark="✓", headline=f"YouTube: {youtube_url}")
            elif not skip_auto_note:
                _append_todo_items(
                    auto_items,
                    mark="!",
                    headline="YouTube: 失败 — https://studio.youtube.com/",
                )
            elif skip_auto_note:
                _append_todo_items(
                    auto_items, mark=" ", headline="YouTube: 将自动投稿（本次未跑）"
                )
        sections.append(("② 完全自动 · 无需再操作", auto_items))

    draft_items: list[tuple[str, str, list[str]]] = []
    draft_on = (
        (bilibili_enabled() and bilibili_article_enabled())
        or zhihu_enabled()
        or xhs_article_enabled()
        or wechat_enabled()
        or tiktok_enabled()
    )
    if draft_on:
        if bilibili_enabled() and bilibili_article_enabled():
            article_url, article_published = _read_bilibili_article_status()
            if skip_auto_note:
                _append_todo_items(
                    draft_items,
                    mark=" ",
                    headline="B站专栏: 发布流程未跑（需论坛包 post.md）",
                    sublines=[
                        "https://member.bilibili.com/platform/upload/text/frame"
                    ],
                )
            elif article_published and article_url:
                _append_todo_items(
                    draft_items,
                    mark="✓",
                    headline=f"B站专栏: 已发布 — {article_url}",
                )
            elif article_url:
                note = "草稿已保存，请打开链接点「发布」"
                if not bilibili_article_auto_publish():
                    note += "（BILIBILI_ARTICLE_AUTO_PUBLISH 未开启）"
                _append_todo_items(
                    draft_items,
                    mark=" ",
                    headline=f"B站专栏: {article_url}",
                    sublines=[note],
                )
            elif bilibili_title and has_forum:
                _append_todo_items(
                    draft_items,
                    mark="!",
                    headline="B站专栏: 未同步 — ./scripts/publish-bilibili.sh --article-only",
                )
            elif bilibili_title:
                _append_todo_items(
                    draft_items, mark="·", headline="B站专栏: 未同步（需论坛包）"
                )
        if zhihu_enabled():
            url = _read_last_log_field("last_zhihu_publish.json", "url") or ZHIHU_DRAFTS_URL
            if skip_auto_note:
                _append_todo_items(
                    draft_items,
                    mark=" ",
                    headline=f"知乎专栏: 将保存草稿后手动发布 — {ZHIHU_DRAFTS_URL}",
                )
            elif zhihu_title:
                _append_todo_items(
                    draft_items,
                    mark=" ",
                    headline=f"知乎专栏: {zhihu_title}",
                    sublines=[f"草稿箱 — {url}", "请手动点发布"],
                )
            else:
                _append_todo_items(
                    draft_items,
                    mark="!",
                    headline=f"知乎专栏: 失败 — ./scripts/publish-zhihu.sh · {ZHIHU_DRAFTS_URL}",
                )
        if xhs_article_enabled():
            if skip_auto_note:
                _append_todo_items(
                    draft_items,
                    mark=" ",
                    headline=f"小红书图文: 将保存本地草稿后手动发布 — {XHS_OPEN_CREATOR_CMD}",
                    sublines=[XHS_MANUAL_PUBLISH_TIP],
                )
            elif xhs_article_title:
                _append_todo_items(
                    draft_items,
                    mark=" ",
                    headline=f"小红书图文: {xhs_article_title}",
                    sublines=[
                        XHS_OPEN_CREATOR_CMD,
                        f"发布页: {XHS_DRAFTS_URL}",
                        XHS_MANUAL_PUBLISH_TIP,
                    ],
                )
            else:
                _append_todo_items(
                    draft_items,
                    mark="!",
                    headline="小红书图文: 失败 — ./scripts/publish-xhs-article.sh",
                    sublines=[f"成功后: {XHS_OPEN_CREATOR_CMD} · {XHS_MANUAL_PUBLISH_TIP}"],
                )
        if wechat_enabled():
            published = _read_last_log_bool("last_wechat_publish.json", "published")
            if skip_auto_note:
                _append_todo_items(
                    draft_items,
                    mark=" ",
                    headline=f"微信公众号: 将存草稿箱后手动发表 — {WECHAT_DRAFTS_URL}",
                )
            elif wechat_title and not published:
                _append_todo_items(
                    draft_items,
                    mark=" ",
                    headline=f"微信公众号: {wechat_title}",
                    sublines=[WECHAT_DRAFTS_URL, "草稿箱 → 手动发表"],
                )
            elif wechat_title:
                _append_todo_items(
                    draft_items, mark="✓", headline=f"微信公众号: 已发表 — {wechat_title}"
                )
            else:
                _append_todo_items(
                    draft_items,
                    mark="!",
                    headline="微信公众号: 失败 — ./scripts/publish-wechat.sh",
                )
        if tiktok_enabled():
            if skip_auto_note:
                _append_todo_items(
                    draft_items,
                    mark=" ",
                    headline="TikTok: 将上传收件箱，App 内粘贴文案后手动发布",
                )
            elif tiktok_url:
                _append_todo_items(
                    draft_items,
                    mark=" ",
                    headline="TikTok: 已上传收件箱",
                    sublines=["打开 App，粘贴终端【TikTok】文案后发布"],
                )
            else:
                _append_todo_items(
                    draft_items,
                    mark="!",
                    headline="TikTok: 未上传 — ./scripts/publish-tiktok.sh",
                )
        sections.append(("③ 草稿 / 长文 · 请手动点发布", draft_items))

    if forum_rel and not has_forum:
        sections.append(
            (
                "提示",
                [
                    (
                        "·",
                        f"未找到论坛图文包 {forum_rel}/post.md，东财/雪球/专栏等可能未同步",
                        [],
                    )
                ],
            )
        )

    return sections


def format_todo_checklist_markdown(
    *,
    video_rel: str,
    forum_rel: str,
    has_forum: bool,
    youtube_url: str = "",
    tiktok_url: str = "",
    bilibili_title: str = "",
    eastmoney_title: str = "",
    xueqiu_title: str = "",
    wechat_title: str = "",
    zhihu_title: str = "",
    xhs_article_title: str = "",
    skip_auto_note: bool = False,
    script: dict | None = None,
) -> str:
    del script  # 预审仅终端输出
    lines = [
        README_TODO_HEADING,
        "",
        "根据当前 `.env` 与本次发布结果生成，打勾表示你已完成。",
        "",
        f"**配置**: {_todo_config_summary()}",
        "",
    ]
    if forum_rel:
        lines.append(f"**论坛图文**: `{forum_rel}/`")
        lines.append("")
    for title, items in build_todo_checklist_items(
        video_rel=video_rel,
        forum_rel=forum_rel,
        has_forum=has_forum,
        youtube_url=youtube_url,
        tiktok_url=tiktok_url,
        bilibili_title=bilibili_title,
        eastmoney_title=eastmoney_title,
        xueqiu_title=xueqiu_title,
        wechat_title=wechat_title,
        zhihu_title=zhihu_title,
        xhs_article_title=xhs_article_title,
        skip_auto_note=skip_auto_note,
    ):
        lines.append(f"### {title}")
        lines.append("")
        for mark, headline, sublines in items:
            box = "x" if mark == "✓" else " "
            lines.append(f"- [{box}] {headline}")
            for sub in sublines:
                lines.append(f"  - {sub}")
        lines.append("")
    lines.append(
        "> 提示：财经平台风控严，简介勿出现「荐股/收益/带单」等字眼。"
    )
    return "\n".join(lines).rstrip() + "\n"


def sync_todo_to_forum_readme(video_path: Path, **todo_kwargs) -> None:
    """把待办清单写入归档目录 README.md（与终端 TODO 一致）。"""
    forum_dir = video_path.parent / video_path.stem
    readme = forum_dir / "README.md"
    if not readme.is_file():
        return
    body = readme.read_text(encoding="utf-8")
    if README_TODO_HEADING in body:
        body = body[: body.index(README_TODO_HEADING)].rstrip() + "\n\n"
    readme.write_text(
        body + format_todo_checklist_markdown(**todo_kwargs),
        encoding="utf-8",
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
    wechat_title: str,
    zhihu_title: str,
    xhs_article_title: str,
    skip_auto_note: bool,
) -> None:
    """流程结束后的「待办清单」：①手动视频 ②自动提交 ③草稿箱待点发布。"""
    print("\n" + "═" * 58, flush=True)
    print("✅ 发布 TODO 清单（①手动视频 · ②自动提交 · ③草稿待发布）", flush=True)
    print(f"   配置: {_todo_config_summary()}", flush=True)
    print("═" * 58, flush=True)

    for title, items in build_todo_checklist_items(
        video_rel=video_rel,
        forum_rel=forum_rel,
        has_forum=has_forum,
        youtube_url=youtube_url,
        tiktok_url=tiktok_url,
        bilibili_title=bilibili_title,
        eastmoney_title=eastmoney_title,
        xueqiu_title=xueqiu_title,
        wechat_title=wechat_title,
        zhihu_title=zhihu_title,
        xhs_article_title=xhs_article_title,
        skip_auto_note=skip_auto_note,
    ):
        print(f"\n— {title} —", flush=True)
        if title.startswith("①"):
            print("  复制上面「标题 / 简介 / 话题」，真人上传后发布：", flush=True)
        for mark, headline, sublines in items:
            bracket = f"[{mark}]" if mark.strip() else "[ ]"
            print(f"  {bracket} {headline}", flush=True)
            for sub in sublines:
                print(f"        {sub}", flush=True)

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


def bilibili_skip_video() -> bool:
    raw = os.environ.get("BILIBILI_SKIP_VIDEO", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


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


def bilibili_article_auto_publish() -> bool:
    raw = os.environ.get("BILIBILI_ARTICLE_AUTO_PUBLISH", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def zhihu_enabled() -> bool:
    value = os.environ.get("AIVIDEO_PUBLISH_ZHIHU")
    if value is None or value.strip() == "":
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def xhs_article_enabled() -> bool:
    value = os.environ.get("AIVIDEO_PUBLISH_XHS_ARTICLE")
    if value is None or value.strip() == "":
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")
