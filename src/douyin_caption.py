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


def _script_text(script: dict | None) -> str:
    """把脚本里的标题/关键词/各页文字拼成一个大字符串，用于关键词匹配。"""
    parts: list[str] = [
        str((script or {}).get("title") or ""),
        str((script or {}).get("keyword") or ""),
    ]
    for slide in (script or {}).get("slides") or []:
        if not isinstance(slide, dict):
            continue
        parts.append(str(slide.get("headline") or ""))
        parts.append(str(slide.get("narration") or ""))
        for label in slide.get("on_image_text") or []:
            parts.append(str(label))
    return " ".join(parts)


# 财报/股市信号词：命中才追加股市类搜索关键词，避免乱加
_FINANCE_SIGNAL = (
    "财报", "业绩", "营收", "净利", "利润", "毛利", "指引", "电话会", "季报", "年报",
    "股价", "股票", "市值", "估值", "净现金", "现金流", "负债", "EPS", "earnings",
    "revenue", "guidance", "营业额", "盈利", "亏损", "美股", "港股", "中概",
)

# 公司 → 关联市场/板块关键词（只在文中出现该公司时才加）
_COMPANY_MARKET = {
    ("蔚来", "NIO"): ["中概股", "港股", "新能源车", "造车新势力"],
    ("小鹏", "XPeng", "XPENG"): ["中概股", "港股", "新能源车", "造车新势力"],
    ("理想", "Li Auto", "理想汽车"): ["中概股", "港股", "新能源车", "造车新势力"],
    ("阿里", "阿里巴巴", "Alibaba"): ["中概股", "港股"],
    ("腾讯", "Tencent"): ["港股"],
    ("百度", "Baidu"): ["中概股", "港股"],
    ("京东", "JD"): ["中概股", "港股"],
    ("拼多多", "PDD", "Temu"): ["中概股", "美股"],
    ("网易", "NetEase"): ["中概股", "港股"],
    ("B站", "哔哩哔哩", "Bilibili"): ["中概股"],
    ("英伟达", "Nvidia", "NVDA"): ["美股", "科技股", "AI概念股"],
    ("苹果", "Apple"): ["美股", "科技股"],
    ("微软", "Microsoft"): ["美股", "科技股"],
    ("特斯拉", "Tesla"): ["美股", "新能源车"],
    ("Meta", "脸书"): ["美股", "科技股"],
    ("谷歌", "Google", "Alphabet"): ["美股", "科技股"],
    ("亚马逊", "Amazon"): ["美股", "科技股"],
}


def _finance_seo_keywords(script: dict | None) -> list[str]:
    """财报/股市类话题：返回相关的搜索热词（命中公司或财报信号才返回）。"""
    text = _script_text(script)
    if not text:
        return []
    is_finance = any(sig.lower() in text.lower() for sig in _FINANCE_SIGNAL)
    keywords: list[str] = []

    def add(k: str) -> None:
        if k and k not in keywords:
            keywords.append(k)

    matched_company = False
    for aliases, markets in _COMPANY_MARKET.items():
        if any(alias.lower() in text.lower() for alias in aliases):
            matched_company = True
            for m in markets:
                add(m)
    # 命中财报信号但没匹配到具体公司时，给一组通用股市热词
    if is_finance and not matched_company:
        for k in ("美股", "港股", "中概股", "财报"):
            add(k)
    elif matched_company and any(s in text for s in ("财报", "业绩", "营收", "净利", "净现金", "财务")):
        add("财报")
    return keywords[:6]


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

    # 财报/股市类话题：追加港股/美股/中概股等相关搜索热词（仅在内容相关时）
    finance_kw = _finance_seo_keywords(script)
    for kw in finance_kw:
        if kw not in tag_parts:
            tag_parts.append(kw)

    seo_terms = _seo_terms(script, tag_parts)
    desc_bits = [raw_title]
    if keyword and keyword not in raw_title:
        desc_bits.append(keyword)
    if finance_kw:
        desc_bits.append(f"相关：{'、'.join(finance_kw)}。")
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
