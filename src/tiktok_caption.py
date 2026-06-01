"""从脚本 JSON 生成 TikTok Direct Post 文案。"""

from __future__ import annotations

import os

from douyin_caption import _env, _strip_hashtag, _strip_urls, build_sau_fields


def build_tiktok_fields(script: dict | None) -> dict:
    """返回 {title, tags}。TikTok 用 title 字段承载 caption（可含 # 与 @）。"""
    base = build_sau_fields(script)
    raw_title = str((script or {}).get("title") or "").strip()
    keyword = str((script or {}).get("keyword") or "").strip()

    tags = [t.strip() for t in (base.get("tags") or "").split(",") if t.strip()]
    for raw in _env("TIKTOK_HASHTAGS", "#AI #finance #stocks #Shorts").split():
        tag = _strip_hashtag(raw)
        if tag and tag not in tags and len(tags) < 8:
            tags.append(tag)

    lines = [raw_title or base.get("title", "")]
    if keyword and keyword not in lines[0]:
        lines.append(keyword)
    brand = _env("AIVIDEO_BRAND_NAME", "AI财知道").replace(" ", "")
    if brand:
        lines.append(f"{brand} — daily AI & finance explainers.")
    disclaimer = _env(
        "TIKTOK_DISCLAIMER",
        "For education only. Not investment advice.",
    )
    if disclaimer:
        lines.append(disclaimer)
    suffix = _strip_urls(_env("TIKTOK_DESC_SUFFIX"))
    if suffix:
        lines.append(suffix)

    caption = _strip_urls("\n".join(x for x in lines if x)).strip()
    hash_line = " ".join(f"#{t}" for t in tags)
    if hash_line:
        caption = f"{caption}\n\n{hash_line}".strip()

    return {
        "title": caption[:2200],
        "tags": tags,
    }
