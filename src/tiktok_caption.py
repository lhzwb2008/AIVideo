"""从脚本 JSON 生成 TikTok Direct Post / 收件箱 caption。"""

from __future__ import annotations

import os

from douyin_caption import _env, _strip_urls
from platform_tags import build_bilingual_tags, format_hashtag_line


def _locale_en() -> bool:
    return os.environ.get("AIVIDEO_LOCALE", "zh").strip().lower() in ("en", "english")


def build_tiktok_fields(script: dict | None) -> dict:
    """返回 {title, tags}。TikTok 用 title 字段承载 caption（可含 # 与 @）。"""
    raw_title = str((script or {}).get("title") or "").strip()
    keyword = str((script or {}).get("keyword") or "").strip()

    if _locale_en():
        extra_default = "#stocks #USmarket #finance #investing #wallstreet"
    else:
        extra_default = "#AI #finance #investing #stockmarket #Shorts"

    tags = build_bilingual_tags(
        script,
        extra_env="TIKTOK_HASHTAGS",
        extra_default=extra_default,
        max_tags=12,
    )

    lines: list[str] = []
    if raw_title:
        lines.append(raw_title)
    elif keyword:
        lines.append(keyword)

    if keyword and keyword not in (lines[0] if lines else ""):
        lines.append(keyword)

    if _locale_en():
        brand = "Market Sketch"
        lines.append(f"{brand} — US markets in plain English.")
    else:
        brand = _env("AIVIDEO_BRAND_NAME", "AI财知道").replace(" ", "")
        if brand:
            lines.append(f"{brand} — 每天一个 AI 与财经热点解读。")

    disclaimer = _env(
        "TIKTOK_DISCLAIMER",
        "For education only. Not investment advice." if _locale_en() else "",
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
