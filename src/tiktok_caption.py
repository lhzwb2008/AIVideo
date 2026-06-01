"""从脚本 JSON 生成 TikTok Direct Post 文案。"""

from __future__ import annotations

import os

from douyin_caption import _env, _strip_urls, build_sau_fields
from platform_tags import build_bilingual_tags, format_hashtag_line


def build_tiktok_fields(script: dict | None) -> dict:
    """返回 {title, tags}。TikTok 用 title 字段承载 caption（可含 # 与 @）。"""
    base = build_sau_fields(script)
    raw_title = str((script or {}).get("title") or "").strip()
    keyword = str((script or {}).get("keyword") or "").strip()

    tags = build_bilingual_tags(
        script,
        extra_env="TIKTOK_HASHTAGS",
        extra_default="#AI #finance #investing #stockmarket #Shorts",
        max_tags=12,
    )

    lines = [raw_title or base.get("title", "")]
    if keyword and keyword not in lines[0]:
        lines.append(keyword)
    brand = _env("AIVIDEO_BRAND_NAME", "AI财知道").replace(" ", "")
    if brand:
        lines.append(f"{brand} — 每天一个 AI 与财经热点解读。")
    disclaimer = _env(
        "TIKTOK_DISCLAIMER",
        "本内容仅供学习交流，不构成投资建议。",
    )
    if disclaimer:
        lines.append(disclaimer)
    suffix = _strip_urls(_env("TIKTOK_DESC_SUFFIX"))
    if suffix:
        lines.append(suffix)

    caption = _strip_urls("\n".join(x for x in lines if x)).strip()
    hash_line = format_hashtag_line(tags)
    if hash_line:
        caption = f"{caption}\n\n{hash_line}".strip()

    return {
        "title": caption[:2200],
        "tags": tags,
    }
