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

# 主流程不会自动发的视频站（始终需要你手动上传）
_VIDEO_MANUAL_PLATFORMS: list[tuple[str, str, str]] = [
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
]

_VIDEO_MANUAL_SHIPINHAO: tuple[str, str, str] = (
    "视频号",
    "https://channels.weixin.qq.com/platform/post/create",
    "短标题（6–16 字），描述贴「简介」，加「话题」",
)


def _video_manual_platforms() -> list[tuple[str, str, str]]:
    from publish_llm_browser import llm_browser_default

    platforms: list[tuple[str, str, str]] = []
    if not (douyin_enabled() and llm_browser_default()):
        platforms.append(_VIDEO_MANUAL_PLATFORMS[0])
    if not (xhs_video_enabled() and llm_browser_default()):
        platforms.append(_VIDEO_MANUAL_PLATFORMS[1])
    if not shipinhao_enabled():
        platforms.append(_VIDEO_MANUAL_SHIPINHAO)
    return platforms

BILIBILI_VIDEO_UPLOAD_URL = (
    "https://member.bilibili.com/platform/upload/video/frame"
)
ZHIHU_DRAFTS_URL = "https://zhuanlan.zhihu.com/creator/manage/drafts"
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
    if _locale_en():
        yt = build_youtube_fields(script)
        tk = build_tiktok_fields(script)
        tags = ", ".join(yt.get("tags") or [])
        return {
            "title": yt["title"],
            "desc": (tk.get("title") or yt.get("description") or "")[:1000],
            "tags": tags,
        }
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
    shipinhao_title: str = "",
    douyin_title: str = "",
    xiaohongshu_title: str = "",
    skip_auto_note: bool = False,
) -> None:
    script = _load_script_dict(script_path) or load_script(script_path)
    fields = build_publish_fields(script)
    tags = fields.get("tags") or ""
    hashtags = " ".join(f"#{t.strip()}" for t in tags.split(",") if t.strip())

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
    if _locale_en():
        print("📋 发布文案（YouTube + TikTok）", flush=True)
    else:
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

    if _locale_en():
        yt_fields = build_youtube_fields(script)
        tk_fields = build_tiktok_fields(script)
        yt_hashtags = " ".join(f"#{t}" for t in yt_fields.get("tags") or [])
        print("\n【YouTube 自动发布】", flush=True)
        print(f"标题: {yt_fields['title']}", flush=True)
        print(f"标签: {', '.join(yt_fields.get('tags') or [])}", flush=True)
        if yt_hashtags:
            print(f"话题: {yt_hashtags}", flush=True)

        print("\n【TikTok】", flush=True)
        tk_ready, tk_reason = _tiktok_direct_post_ready()
        if not tiktok_enabled():
            print("（未开启 AIVIDEO_PUBLISH_TIKTOK）", flush=True)
        elif tk_ready:
            print("（Direct Post 自动发布，下方为 caption 预览）", flush=True)
        else:
            print(f"（已跳过自动发布：{tk_reason}）", flush=True)
            print("下方文案仅供参考；过审后 ./tiktok-login.sh --force 重新授权即可自动发。", flush=True)
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
        shipinhao_title=shipinhao_title,
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


README_TODO_HEADING = "## 发布 TODO 清单"


def _locale_en() -> bool:
    return os.environ.get("AIVIDEO_LOCALE", "zh").strip().lower() in ("en", "english")


def _env_enabled(name: str, *, default: str = "0") -> bool:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        value = default
    return value.strip().lower() in ("1", "true", "yes", "on")


def douyin_enabled() -> bool:
    return _env_enabled("AIVIDEO_PUBLISH_DOUYIN", default="0")


def xhs_video_enabled() -> bool:
    return _env_enabled("AIVIDEO_PUBLISH_XHS", default="0")


def shipinhao_enabled() -> bool:
    return _env_enabled("AIVIDEO_PUBLISH_SHIPINHAO", default="0")


def bilibili_video_auto_enabled() -> bool:
    return bilibili_enabled() and not bilibili_skip_video()


def bilibili_video_manual_needed() -> bool:
    """是否在待办中提醒手动上传 B 站视频。"""
    if bilibili_video_auto_enabled():
        return False
    return bilibili_skip_video() and bilibili_enabled()


def _todo_config_summary() -> str:
    """当前 .env 中与待办相关的开关摘要。"""
    from publish_llm_browser import llm_browser_default

    if _locale_en():
        bits: list[str] = []
        if youtube_enabled():
            bits.append("YouTube=自动")
        if tiktok_enabled():
            label = _tiktok_config_label()
            if label:
                bits.append(label)
        return " · ".join(bits) if bits else "（未开启 YouTube/TikTok）"

    bits = []
    if douyin_enabled() and llm_browser_default():
        bits.append("抖音=LLM自动")
    else:
        bits.append("抖音=你手动传")
    if xhs_video_enabled() and llm_browser_default():
        bits.append("小红书=LLM自动")
    else:
        bits.append("小红书=你手动传")
    if shipinhao_enabled():
        bits.append("视频号=LLM自动")
    if bilibili_video_auto_enabled():
        bits.append("B站视频=自动")
    elif bilibili_video_manual_needed():
        bits.append("B站视频=手动传")
    if eastmoney_enabled():
        bits.append("东财=自动")
    if xueqiu_enabled():
        bits.append("雪球=自动")
    if wechat_enabled():
        bits.append("公众号=草稿+你点发表")
    if zhihu_enabled():
        if zhihu_auto_publish():
            bits.append("知乎专栏=自动")
        else:
            bits.append("知乎专栏=草稿+你点发布")
    return " · ".join(bits) if bits else "（.env 未开启任何发布渠道）"


def _append_todo_items(
    items: list[tuple[str, str, list[str]]],
    *,
    mark: str,
    headline: str,
    sublines: list[str] | None = None,
) -> None:
    items.append((mark, headline, sublines or []))


def _auto_publish_summary(
    *,
    youtube_url: str,
    tiktok_url: str,
    bilibili_title: str,
    eastmoney_title: str,
    xueqiu_title: str,
    wechat_title: str,
    zhihu_title: str,
    shipinhao_title: str,
    skip_auto_note: bool,
) -> str:
    """已成功自动发布的平台，仅作一行摘要，不进待办清单。"""
    if skip_auto_note:
        return ""
    names: list[str] = []
    if shipinhao_enabled() and shipinhao_title:
        names.append("视频号")
    if bilibili_video_auto_enabled() and bilibili_title:
        names.append("B站视频")
    if eastmoney_enabled() and eastmoney_title:
        names.append("东财")
    if xueqiu_enabled() and xueqiu_title:
        names.append("雪球")
    if _locale_en() and youtube_enabled() and youtube_url:
        names.append("YouTube")
    if _locale_en() and tiktok_enabled() and tiktok_url:
        names.append("TikTok")
    if wechat_enabled() and wechat_title:
        if _read_last_log_bool("last_wechat_publish.json", "published"):
            names.append("公众号")
    if zhihu_enabled() and zhihu_title:
        if zhihu_auto_publish() and _read_last_log_bool("last_zhihu_publish.json", "published"):
            names.append("知乎")
        elif not zhihu_auto_publish():
            names.append("知乎草稿")
    if not names:
        return ""
    return f"（{'、'.join(names)} 已自动处理 ✓）"


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
    shipinhao_title: str,
    skip_auto_note: bool,
) -> list[tuple[str, list[tuple[str, str, list[str]]]]]:
    """仅生成仍需你亲手完成的待办，已成功自动发布的不列入。"""
    manual: list[tuple[str, str, list[str]]] = []
    notes: list[tuple[str, str, list[str]]] = []

    if skip_auto_note:
        _append_todo_items(
            notes,
            mark="·",
            headline="本次未执行自动发布（--no-publish / --dry-run）",
        )

    if video_rel and not _locale_en():
        for name, url, tip in _video_manual_platforms():
            sublines = [tip, f"成片: {video_rel}"]
            _append_todo_items(
                manual,
                mark=" ",
                headline=f"{name}: {url}",
                sublines=sublines,
            )

    if bilibili_video_manual_needed():
        sublines = ["复制上面「标题 / 简介 / 话题」，在创作中心上传成片"]
        if bilibili_skip_video():
            sublines.append(".env 已设 BILIBILI_SKIP_VIDEO=1")
        if video_rel:
            sublines.append(f"成片: {video_rel}")
        _append_todo_items(
            manual,
            mark=" ",
            headline=f"B站视频: {BILIBILI_VIDEO_UPLOAD_URL}",
            sublines=sublines,
        )

    if bilibili_video_auto_enabled() and not skip_auto_note and not bilibili_title:
        _append_todo_items(
            manual,
            mark="!",
            headline="B站视频: 自动投稿失败 — ./scripts/publish-bilibili.sh",
        )

    if shipinhao_enabled() and not skip_auto_note and not shipinhao_title:
        _append_todo_items(
            manual,
            mark="!",
            headline="视频号: 自动发布失败 — ./scripts/publish-shipinhao.sh",
        )

    if eastmoney_enabled() and not skip_auto_note and not eastmoney_title:
        _append_todo_items(
            manual,
            mark="!",
            headline="东方财富: 自动发布失败 — ./scripts/publish-eastmoney.sh",
        )

    if xueqiu_enabled() and not skip_auto_note and not xueqiu_title:
        _append_todo_items(
            manual,
            mark="!",
            headline="雪球: 自动发布失败 — ./scripts/publish-xueqiu.sh",
        )

    if _locale_en() and youtube_enabled() and not skip_auto_note and not youtube_url:
        _append_todo_items(
            manual,
            mark="!",
            headline="YouTube: 自动发布失败 — https://studio.youtube.com/",
        )

    if zhihu_enabled():
        published = _read_last_log_bool("last_zhihu_publish.json", "published")
        url = _read_last_log_field("last_zhihu_publish.json", "url") or ZHIHU_DRAFTS_URL
        if skip_auto_note:
            if zhihu_auto_publish():
                _append_todo_items(
                    manual,
                    mark=" ",
                    headline="知乎专栏: 将自动填表并发布",
                )
            else:
                _append_todo_items(
                    manual,
                    mark=" ",
                    headline=f"知乎专栏: 将保存草稿，你需手动点发布 — {ZHIHU_DRAFTS_URL}",
                )
        elif zhihu_title and zhihu_auto_publish() and published:
            pass
        elif zhihu_title and not zhihu_auto_publish():
            _append_todo_items(
                manual,
                mark=" ",
                headline=f"知乎专栏: {zhihu_title}",
                sublines=[f"草稿箱 — {url}", "请手动点发布"],
            )
        elif zhihu_title and zhihu_auto_publish() and not published:
            _append_todo_items(
                manual,
                mark=" ",
                headline=f"知乎专栏: {zhihu_title}",
                sublines=[f"自动发布未确认，请检查 — {url}", "或 ./scripts/publish-zhihu.sh --publish"],
            )
        else:
            _append_todo_items(
                manual,
                mark="!",
                headline=f"知乎专栏: 失败 — ./scripts/publish-zhihu.sh · {ZHIHU_DRAFTS_URL}",
            )

    if wechat_enabled():
        published = _read_last_log_bool("last_wechat_publish.json", "published")
        if skip_auto_note:
            _append_todo_items(
                manual,
                mark=" ",
                headline=f"微信公众号: 将存草稿箱，你需手动发表 — {WECHAT_DRAFTS_URL}",
            )
        elif wechat_title and not published:
            _append_todo_items(
                manual,
                mark=" ",
                headline=f"微信公众号: {wechat_title}",
                sublines=[WECHAT_DRAFTS_URL, "草稿箱 → 手动发表"],
            )
        elif wechat_title and published:
            pass
        else:
            _append_todo_items(
                manual,
                mark="!",
                headline="微信公众号: 失败 — ./scripts/publish-wechat.sh",
            )

    if _locale_en() and tiktok_enabled():
        tk_ready, tk_reason = _tiktok_direct_post_ready()
        if skip_auto_note:
            if tk_ready:
                _append_todo_items(
                    notes,
                    mark="·",
                    headline="TikTok: 将 Direct Post 自动发布",
                )
            else:
                _append_todo_items(
                    notes,
                    mark="·",
                    headline=f"TikTok: 将跳过自动发布（{tk_reason}）",
                )
        elif tiktok_url:
            pass
        elif not tk_ready:
            _append_todo_items(
                notes,
                mark="·",
                headline=f"TikTok: 已跳过自动发布（{tk_reason}）",
            )
        else:
            _append_todo_items(
                manual,
                mark="!",
                headline="TikTok: 自动发布失败 — ./scripts/publish-tiktok.sh",
            )

    if forum_rel and not has_forum and (
        eastmoney_enabled()
        or xueqiu_enabled()
        or zhihu_enabled()
        or wechat_enabled()
    ):
        _append_todo_items(
            notes,
            mark="·",
            headline=f"未找到论坛图文包 {forum_rel}/post.md，东财/雪球/知乎等可能未同步",
        )

    sections: list[tuple[str, list[tuple[str, str, list[str]]]]] = []
    if manual:
        sections.append((f"待你亲手发布（{len(manual)} 项）", manual))
    if notes:
        sections.append(("说明", notes))
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
    shipinhao_title: str = "",
    skip_auto_note: bool = False,
    script: dict | None = None,
) -> str:
    del script  # 预审仅终端输出
    auto_line = _auto_publish_summary(
        youtube_url=youtube_url,
        tiktok_url=tiktok_url,
        bilibili_title=bilibili_title,
        eastmoney_title=eastmoney_title,
        xueqiu_title=xueqiu_title,
        wechat_title=wechat_title,
        zhihu_title=zhihu_title,
        shipinhao_title=shipinhao_title,
        skip_auto_note=skip_auto_note,
    )
    lines = [
        README_TODO_HEADING,
        "",
        "仅列出仍需你亲手完成的发布步骤；脚本已自动处理的平台不列入待办。",
        "",
        f"**配置**: {_todo_config_summary()}",
        "",
    ]
    if auto_line:
        lines.append(auto_line)
        lines.append("")
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
        shipinhao_title=shipinhao_title,
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
    if _locale_en():
        lines.append("> For education only. Not investment advice.")
    else:
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
    shipinhao_title: str,
    skip_auto_note: bool,
) -> None:
    """流程结束后的待办清单：优先列出需要你亲手完成的事项。"""
    sections = build_todo_checklist_items(
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
        shipinhao_title=shipinhao_title,
        skip_auto_note=skip_auto_note,
    )
    manual_n = 0
    for title, items in sections:
        if title.startswith("待你亲手发布"):
            manual_n = len(items)

    auto_line = _auto_publish_summary(
        youtube_url=youtube_url,
        tiktok_url=tiktok_url,
        bilibili_title=bilibili_title,
        eastmoney_title=eastmoney_title,
        xueqiu_title=xueqiu_title,
        wechat_title=wechat_title,
        zhihu_title=zhihu_title,
        shipinhao_title=shipinhao_title,
        skip_auto_note=skip_auto_note,
    )

    print("\n" + "═" * 58, flush=True)
    print(f"📌 发布 TODO · 待你亲手完成 {manual_n} 项", flush=True)
    print(f"   配置: {_todo_config_summary()}", flush=True)
    if auto_line:
        print(f"   {auto_line}", flush=True)
    print("═" * 58, flush=True)

    for title, items in sections:
        print(f"\n— {title} —", flush=True)
        if title.startswith("待你亲手发布") and not _locale_en():
            print("  复制上面「标题 / 简介 / 话题」，按链接上传后发布：", flush=True)
        for mark, headline, sublines in items:
            bracket = f"[{mark}]" if mark.strip() else "[ ]"
            print(f"  {bracket} {headline}", flush=True)
            for sub in sublines:
                print(f"        {sub}", flush=True)

    print("\n" + "─" * 58, flush=True)
    if _locale_en():
        print("提示: For education only. Not investment advice.", flush=True)
    else:
        print("提示: 财经平台风控严，简介勿出现「荐股/收益/带单」等字眼。", flush=True)
        if script:
            try:
                from research import print_douyin_pre_publish_scan

                print_douyin_pre_publish_scan(script)
            except Exception:
                pass
    print("─" * 58 + "\n", flush=True)


def youtube_enabled() -> bool:
    return _env_enabled("AIVIDEO_PUBLISH_YOUTUBE", default="0")


def tiktok_enabled() -> bool:
    return _env_enabled("AIVIDEO_PUBLISH_TIKTOK", default="0")


def _tiktok_direct_post_ready() -> tuple[bool, str]:
    try:
        from tiktok_auth import tiktok_direct_post_ready

        return tiktok_direct_post_ready()
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def _tiktok_config_label() -> str:
    if not tiktok_enabled():
        return ""
    ready, _ = _tiktok_direct_post_ready()
    return "TikTok=自动" if ready else "TikTok=跳过"


def instagram_enabled() -> bool:
    return _env_enabled("AIVIDEO_PUBLISH_INSTAGRAM", default="0")


def facebook_enabled() -> bool:
    return _env_enabled("AIVIDEO_PUBLISH_FACEBOOK", default="0")


def linkedin_enabled() -> bool:
    return _env_enabled("AIVIDEO_PUBLISH_LINKEDIN", default="0")


def us_social_enabled() -> bool:
    return instagram_enabled() or facebook_enabled() or linkedin_enabled()


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


def zhihu_enabled() -> bool:
    value = os.environ.get("AIVIDEO_PUBLISH_ZHIHU")
    if value is None or value.strip() == "":
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def zhihu_auto_publish() -> bool:
    raw = os.environ.get("ZHIHU_AUTO_PUBLISH", "0").strip().lower()
    return raw in ("1", "true", "yes", "on")
