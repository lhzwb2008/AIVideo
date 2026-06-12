"""为小红书 / 视频号生成发布文案（标题/简介/标签）。

复用 douyin_caption 里已沉淀的选题关键词逻辑，但按各平台习惯做风格适配：
- 小红书：标题短（≤20 字）带情绪钩子，正文 emoji + 行内 #话题，无外链导流。
- 视频号：需要一个 6–16 字的短标题（short_title）。
"""

from __future__ import annotations

import re

from douyin_caption import (
    _env,
    _normalize_publish_tags,
    _strip_urls,
    _build_publish_desc,
)

# 各平台标题硬上限（留点余量，避免平台侧再截断把话说半截）
TITLE_LIMIT = {
    "xiaohongshu": 20,
    "tencent": 22,
}

# 视频号短标题 6–16 字
TENCENT_SHORT_TITLE_MIN = 6
TENCENT_SHORT_TITLE_MAX = 16


def _clean_title(text: str) -> str:
    text = _strip_urls(text)
    text = re.sub(r"^【[^】]*】", "", text)  # 去掉抖音常用的【品牌】前缀
    return text.strip()


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("，。、：；,. ") + "…"


def build_social_fields(script: dict | None, platform: str) -> dict:
    """返回 {title, desc, tags(list[str]), short_title}。

    platform ∈ {xiaohongshu, tencent}
    """
    platform = platform.lower()
    brand = _env("AIVIDEO_BRAND_NAME", "AI财知道").replace(" ", "")
    keyword = str((script or {}).get("keyword") or "").strip()
    raw_title = _clean_title(
        str((script or {}).get("title") or keyword or "AI财经热点")
    )

    title = _truncate(raw_title, TITLE_LIMIT.get(platform, 20))

    topic_kw = _normalize_publish_tags(script)
    tags = topic_kw[:3]

    desc = _build_publish_desc(script, raw_title, brand)
    if platform == "xiaohongshu":
        desc = _truncate(desc, 1000)

    short_title = _truncate(raw_title, TENCENT_SHORT_TITLE_MAX)
    if len(short_title) < TENCENT_SHORT_TITLE_MIN and brand:
        short_title = _truncate(f"{brand}·{short_title}", TENCENT_SHORT_TITLE_MAX)

    return {
        "title": title,
        "desc": desc,
        "tags": tags,
        "short_title": short_title,
    }
