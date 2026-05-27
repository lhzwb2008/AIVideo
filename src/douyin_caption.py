"""从 last_script.json 生成抖音发布所需的标题/简介/标签。"""

from __future__ import annotations

import os
import re


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _strip_hashtag(tag: str) -> str:
    return re.sub(r"^#+", "", tag.strip())


def _format_source(source: object) -> str:
    if isinstance(source, dict):
        url = str(source.get("url") or "").strip()
        title = str(source.get("title") or "").strip()
        if url and title:
            return f"{title} ({url})"
        return url or title
    return str(source or "").strip()


def build_sau_fields(script: dict | None) -> dict[str, str]:
    """返回 publish-douyin 用的 title、desc、tags（逗号分隔，无 #）。"""
    brand = _env("AIVIDEO_BRAND_NAME", "AI财知道").replace(" ", "")
    keyword = (script or {}).get("keyword", "").strip()
    raw_title = ((script or {}).get("title") or keyword or "AI财经热点").strip()
    title = raw_title if brand and brand in raw_title else (f"【{brand}】{raw_title}" if brand else raw_title)
    source = _format_source((script or {}).get("source"))

    tag_parts: list[str] = []
    if brand:
        tag_parts.append(brand)
    if keyword:
        tag_parts.append(_strip_hashtag(keyword.replace(" ", "")))
    for raw in _env("DOUYIN_HASHTAGS", "#AI #人工智能 #财经 #美股 #中概股").split():
        t = _strip_hashtag(raw)
        if t and t not in tag_parts:
            tag_parts.append(t)

    desc_bits = [raw_title]
    if keyword and keyword not in raw_title:
        desc_bits.append(keyword)
    if brand:
        desc_bits.append(f"——{brand}，每天一个 AI 财经为什么，点关注追更新。")
    if source:
        desc_bits.append(f"参考：{source}")
    extra = _env("DOUYIN_DESC_SUFFIX")
    if extra:
        desc_bits.append(extra)

    return {
        "title": title[:100],
        "desc": " ".join(desc_bits)[:1000],
        "tags": ",".join(tag_parts[:10]),
    }
