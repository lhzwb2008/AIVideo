"""为小红书 / 快手 / 视频号生成发布文案（标题/简介/标签）。

复用 douyin_caption 里已沉淀的选题关键词逻辑，但按各平台习惯做风格适配：
- 小红书：标题短（≤20 字）带情绪钩子，正文 emoji + 行内 #话题，无外链导流。
- 快手：标题口语化，正文偏简短。
- 视频号：需要一个 6–16 字的短标题（short_title）。
"""

from __future__ import annotations

import re

from douyin_caption import (
    _env,
    _strip_hashtag,
    _strip_urls,
    _topic_keywords,
)

# 各平台标题硬上限（留点余量，避免平台侧再截断把话说半截）
TITLE_LIMIT = {
    "xiaohongshu": 20,
    "kuaishou": 28,
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


def _evergreen_tags(platform: str) -> list[str]:
    env_key = {
        "xiaohongshu": "XHS_HASHTAGS",
        "kuaishou": "KUAISHOU_HASHTAGS",
        "tencent": "SHIPINHAO_HASHTAGS",
    }.get(platform, "")
    raw = _env(env_key, "#财经 #股市 #投资 #AI")
    return [t for t in (_strip_hashtag(x) for x in raw.split()) if t]


def _desc_suffix(platform: str) -> str:
    env_key = {
        "xiaohongshu": "XHS_DESC_SUFFIX",
        "kuaishou": "KUAISHOU_DESC_SUFFIX",
        "tencent": "SHIPINHAO_DESC_SUFFIX",
    }.get(platform, "")
    return _strip_urls(_env(env_key))


def build_social_fields(script: dict | None, platform: str) -> dict:
    """返回 {title, desc, tags(list[str]), short_title}。

    platform ∈ {xiaohongshu, kuaishou, tencent}
    """
    platform = platform.lower()
    brand = _env("AIVIDEO_BRAND_NAME", "AI财知道").replace(" ", "")
    keyword = str((script or {}).get("keyword") or "").strip()
    raw_title = _clean_title(
        str((script or {}).get("title") or keyword or "AI财经热点")
    )

    title = _truncate(raw_title, TITLE_LIMIT.get(platform, 20))

    # 内容关键词（大模型写好的 script.hashtags 优先，否则启发式财经词）
    topic_kw = _topic_keywords(script)
    tags: list[str] = []
    for kw in topic_kw:
        if kw and kw not in tags:
            tags.append(kw)
    for kw in _evergreen_tags(platform):
        if kw and kw not in tags:
            tags.append(kw)
    if brand and brand not in tags:
        tags.append(brand)
    tags = tags[:10]

    # 正文：钩子标题 + 关键词 + CTA + 行内话题（小红书/视频号习惯把 #话题 写进正文）
    if platform == "xiaohongshu":
        bits = [f"{raw_title} 📈"]
        if keyword and keyword not in raw_title:
            bits.append(f"今天聊聊「{keyword}」。")
        bits.append(f"💡 {brand}：每天一个 AI 和股市的为什么，A股·美股·港股都聊。")
        bits.append("⚠️ 内容仅为信息分享，不构成投资建议。")
    else:
        bits = [raw_title]
        if keyword and keyword not in raw_title:
            bits.append(f"关键词：{keyword}。")
        bits.append(f"{brand}，每天一个 AI 和股市的为什么，点关注追更新。")

    suffix = _desc_suffix(platform)
    if suffix:
        bits.append(suffix)

    # 注意：不在正文里内联 #标签——各平台 uploader 会单独用 tags 列表去填话题，
    # 否则会和正文里的 # 文本重复。
    desc = _strip_urls(" ".join(bits)).strip()[:1000]

    short_title = _truncate(raw_title, TENCENT_SHORT_TITLE_MAX)
    if len(short_title) < TENCENT_SHORT_TITLE_MIN and brand:
        short_title = _truncate(f"{brand}·{short_title}", TENCENT_SHORT_TITLE_MAX)

    return {
        "title": title,
        "desc": desc,
        "tags": tags,
        "short_title": short_title,
    }
