"""从脚本 JSON 生成 YouTube Shorts 发布字段（标题 / 描述 / 标签）。"""

from __future__ import annotations

import os

from douyin_caption import _env, _strip_urls, _topic_keywords
from platform_tags import build_bilingual_tags, format_hashtag_line


def _shorts_tag_enabled() -> bool:
    value = os.environ.get("YOUTUBE_ADD_SHORTS_TAG", "1").strip().lower()
    return value not in ("0", "false", "no", "off")


def _locale_en() -> bool:
    return os.environ.get("AIVIDEO_LOCALE", "zh").strip().lower() in ("en", "english")


def build_youtube_fields(script: dict | None) -> dict:
    """返回 {title, description, tags}。tags 为 list[str]，不含 #。"""
    brand_default = "Market Sketch" if _locale_en() else "AI财知道"
    brand = _env("AIVIDEO_BRAND_NAME", brand_default).replace(" ", "")
    keyword = str((script or {}).get("keyword") or "").strip()
    raw_title = str((script or {}).get("title") or keyword or "AI财经热点").strip()
    prefix = _env("YOUTUBE_TITLE_PREFIX", "0").lower()
    if prefix in ("1", "true", "yes", "on") and brand and brand not in raw_title:
        title = f"{brand} | {raw_title}"
    else:
        title = raw_title

    topic_kw = _topic_keywords(script)
    tag_parts = build_bilingual_tags(
        script,
        extra_env="YOUTUBE_HASHTAGS",
        extra_default="#AI #finance #investing #stockmarket #Shorts",
        max_tags=15,
    )
    if _shorts_tag_enabled() and "Shorts" not in tag_parts and "shorts" not in [
        x.lower() for x in tag_parts
    ]:
        tag_parts.append("Shorts")

    desc_lines: list[str] = []
    if _shorts_tag_enabled():
        desc_lines.append("#Shorts")
    desc_lines.append(raw_title)
    if keyword and keyword not in raw_title:
        desc_lines.append(keyword)
    if topic_kw:
        desc_lines.append(f"相关：{'、'.join(topic_kw)}")
    if brand:
        if _locale_en():
            desc_lines.append(f"{brand} — US markets explained in plain English. Subscribe for daily breakdowns.")
        else:
            desc_lines.append(
                f"{brand} — 每天一个 AI 与财经热点解读，A股、美股、港股都聊。欢迎订阅。"
            )
    hash_line = format_hashtag_line(tag_parts)
    if hash_line:
        desc_lines.append(hash_line)
    disclaimer = _env(
        "YOUTUBE_DISCLAIMER",
        "For education only. Not investment advice." if _locale_en() else "本内容仅供学习交流，不构成投资建议。",
    )
    if disclaimer:
        desc_lines.append(disclaimer)
    extra = _strip_urls(_env("YOUTUBE_DESC_SUFFIX"))
    if extra:
        desc_lines.append(extra)

    description = "\n\n".join(_strip_urls(line) for line in desc_lines if line.strip())

    return {
        "title": title[:100],
        "description": description[:5000],
        "tags": tag_parts[:30],
    }
