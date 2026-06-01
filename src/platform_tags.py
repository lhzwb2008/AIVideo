"""各平台发布标签：脚本中文话题 + 对应英文检索词 + 环境变量补充。"""

from __future__ import annotations

from douyin_caption import _env, _strip_hashtag, _topic_keywords

# 中文话题 → 英文 hashtag（无空格，便于 TikTok/YouTube 检索）
_CN_TO_EN: dict[str, str] = {
    "财报": "financialreport",
    "财务报表": "financialstatements",
    "基本面分析": "fundamentalanalysis",
    "A股": "chinaStocks",
    "投资入门": "investing101",
    "大模型": "LLM",
    "人工智能": "AI",
    "AI": "AI",
    "豆包": "Doubao",
    "ChatGPT": "ChatGPT",
    "美股": "USstocks",
    "港股": "HKstocks",
    "中概股": "ChinaStocks",
    "财经": "finance",
    "股市": "stockmarket",
    "投资": "investing",
    "科技股": "techstocks",
    "新能源车": "EV",
}


def build_bilingual_tags(
    script: dict | None,
    *,
    extra_env: str,
    extra_default: str,
    max_tags: int = 15,
) -> list[str]:
    tags: list[str] = []

    def add(tag: str) -> None:
        clean = _strip_hashtag(str(tag or "").strip())
        if clean and clean not in tags and len(tags) < max_tags:
            tags.append(clean)

    for cn in _topic_keywords(script):
        add(cn)
        en = _CN_TO_EN.get(cn)
        if en:
            add(en)

    for raw in _env(extra_env, extra_default).split():
        add(raw)

    return tags


def format_hashtag_line(tags: list[str]) -> str:
    return " ".join(f"#{t}" for t in tags if t)
