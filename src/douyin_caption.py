"""从 last_script.json 生成抖音发布所需的标题/简介/标签。"""

from __future__ import annotations

import os
import re


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _strip_hashtag(tag: str) -> str:
    return re.sub(r"^#+", "", tag.strip())


def _strip_urls(text: str) -> str:
    """抖音作品描述禁止外链，避免被判导流。"""
    text = re.sub(r"\s*参考[:：].*?(?=\s+#|$)", " ", text, flags=re.S)
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"www\.\S+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _append_unique(items: list[str], value: str, *, max_len: int = 16) -> None:
    value = _strip_hashtag(_strip_urls(value)).strip(" ，。、：；,.!?！？")
    if not value or len(value) > max_len or value in items:
        return
    items.append(value)


def _seo_terms(script: dict | None, tag_parts: list[str]) -> list[str]:
    """从脚本内容抽短关键词，只用于站内 SEO，不使用来源和外链。"""
    terms: list[str] = []
    for tag in tag_parts:
        _append_unique(terms, tag)

    for value in (
        (script or {}).get("keyword"),
        (script or {}).get("title"),
    ):
        _append_unique(terms, str(value or ""), max_len=18)

    slides = (script or {}).get("slides")
    if isinstance(slides, list):
        for slide in slides:
            if not isinstance(slide, dict):
                continue
            _append_unique(terms, str(slide.get("headline") or ""))
            for label in slide.get("on_image_text") or []:
                _append_unique(terms, str(label), max_len=12)
            if len(terms) >= 8:
                break
    return terms[:8]


def build_sau_fields(script: dict | None) -> dict[str, str]:
    """返回 publish-douyin 用的 title、desc、tags（逗号分隔，无 #）。"""
    brand = _env("AIVIDEO_BRAND_NAME", "AI财知道").replace(" ", "")
    keyword = (script or {}).get("keyword", "").strip()
    raw_title = ((script or {}).get("title") or keyword or "AI财经热点").strip()
    title = raw_title if brand and brand in raw_title else (f"【{brand}】{raw_title}" if brand else raw_title)

    tag_parts: list[str] = []
    if brand:
        tag_parts.append(brand)
    if keyword:
        tag_parts.append(_strip_hashtag(keyword.replace(" ", "")))
    for raw in _env("DOUYIN_HASHTAGS", "#AI #人工智能 #财经 #美股 #中概股").split():
        t = _strip_hashtag(raw)
        if t and t not in tag_parts:
            tag_parts.append(t)

    seo_terms = _seo_terms(script, tag_parts)
    desc_bits = [raw_title]
    if keyword and keyword not in raw_title:
        desc_bits.append(keyword)
    if seo_terms:
        desc_bits.append(f"关注关键词：{'、'.join(seo_terms)}。")
    if brand:
        desc_bits.append(f"——{brand}，每天一个 AI 财经为什么，点关注追更新。")
    extra = _env("DOUYIN_DESC_SUFFIX")
    if extra:
        desc_bits.append(_strip_urls(extra))

    return {
        "title": title[:100],
        "desc": _strip_urls(" ".join(desc_bits))[:1000],
        "tags": ",".join(tag_parts[:10]),
    }
