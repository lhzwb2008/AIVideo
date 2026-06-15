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


def _subject_text(script: dict | None) -> str:
    """只取「主角」文本：标题 + 关键词 + 封面/各页大标题（headline）。
    用于判断本视频真正在讲谁，避免口播里顺带提一句别的公司就被加一堆无关标签。"""
    parts: list[str] = [
        str((script or {}).get("title") or ""),
        str((script or {}).get("keyword") or ""),
    ]
    for slide in (script or {}).get("slides") or []:
        if isinstance(slide, dict):
            parts.append(str(slide.get("headline") or ""))
    return " ".join(parts)


# 财报/股市信号词：命中才追加股市类搜索关键词，避免乱加
_FINANCE_SIGNAL = (
    "财报", "业绩", "营收", "净利", "利润", "毛利", "指引", "电话会", "季报", "年报",
    "股价", "股票", "市值", "估值", "净现金", "现金流", "负债", "EPS", "earnings",
    "revenue", "guidance", "营业额", "盈利", "亏损", "美股", "港股", "中概",
)

# A股 信号词：命中即判定为 A股 话题
_ASTOCK_SIGNAL = (
    "a股", "涨停", "跌停", "龙虎榜", "游资", "科创板", "创业板", "北向", "北交所",
    "沪深", "沪指", "深成指", "创业板指", "科创50", "连板", "妖股", "人气股",
    "打板", "主力资金", "两市", "沪市", "深市", "题材股", "概念股", "集合竞价", "深股通", "沪股通",
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
    """股市类话题：只返回与本视频**主角**强相关的少量搜索热词（最多 3 个）。

    只看「主角文本」（标题+关键词+各页大标题），不扫口播全文，避免顺带提一句别的
    公司就被加无关标签。原则：宁少勿多，A股 只给「A股」，公司只给其所属市场。
    """
    subject = _subject_text(script).lower()
    if not subject:
        return []
    keywords: list[str] = []

    def add(k: str) -> None:
        if k and k not in keywords:
            keywords.append(k)

    # 1) A股 话题：只加「A股」，板块靠脚本 keyword 自带，不再堆砌
    if any(sig in subject for sig in _ASTOCK_SIGNAL):
        add("A股")
        return keywords[:3]

    # 2) 港美股/中概：只认主角文本里出现的公司，给其所属市场
    for aliases, markets in _COMPANY_MARKET.items():
        if any(alias.lower() in subject for alias in aliases):
            for m in markets:
                add(m)

    # 3) 没匹配到公司但主角明显是财报/股市话题：按主角里出现的市场补一个
    if not keywords and any(sig.lower() in subject for sig in _FINANCE_SIGNAL):
        for mkt in ("美股", "港股", "中概"):
            if mkt in subject:
                add("中概股" if mkt == "中概" else mkt)
        if not keywords:
            add("财报")
    return keywords[:3]


def _cover_subtitle(script: dict | None) -> str:
    for slide in (script or {}).get("slides") or []:
        if isinstance(slide, dict):
            sub = str(slide.get("subtitle") or "").strip()
            if sub:
                return sub
    return ""


# 泛标签/营销词：发布时不自动补，模型写了也过滤掉
_GENERIC_NOISE_TAGS = frozenset({
    "ai", "财经", "复盘", "收盘", "行情", "投资", "标签", "热点",
})


def _normalize_publish_tags(script: dict | None, *, max_tags: int = 3) -> list[str]:
    """2–3 个话题：优先内容词，必要时补 A股/股市，不写 AI 等泛标签。"""
    kws = _topic_keywords(script)
    tags: list[str] = []
    for t in kws:
        if not t or t.lower() in _GENERIC_NOISE_TAGS:
            continue
        if t not in tags:
            tags.append(t)
        if len(tags) >= max_tags:
            return tags

    subject = _subject_text(script).lower()
    if any(sig in subject for sig in _ASTOCK_SIGNAL) and "A股" not in tags:
        tags.insert(0, "A股")
    for mkt in ("美股", "港股", "中概股"):
        if mkt in subject and mkt not in tags:
            tags.append(mkt)
            break
    if "股市" not in tags and len(tags) < max_tags:
        tags.append("股市")
    return tags[:max_tags]


def _build_publish_desc(script: dict | None, raw_title: str, brand: str) -> str:
    """简介：封面副标题或标题 + 短品牌署名，无关键词堆砌。"""
    subtitle = _cover_subtitle(script)
    lead = subtitle or raw_title
    bits = [lead]
    if brand:
        bits.append(f"——{brand}")
    return _strip_urls(" ".join(bits))


def _topic_keywords(script: dict | None) -> list[str]:
    """本条视频的内容关键词：优先 script.hashtags；旧脚本回退启发式。最多 3 个。"""
    raw = (script or {}).get("hashtags")
    kws: list[str] = []
    if isinstance(raw, list):
        for t in raw:
            _append_unique(kws, str(t or ""), max_len=14)
    if not kws:  # 兼容旧脚本
        kws = _finance_seo_keywords(script)
    return kws[:3]


def build_sau_fields(script: dict | None) -> dict[str, str]:
    """返回 publish-douyin 用的 title、desc、tags（逗号分隔，无 #）。

    内容关键词由选题/脚本生成阶段的大模型按内容写好（script.hashtags），这里不再写死。
    """
    brand = _env("AIVIDEO_BRAND_NAME", "AI财知道").replace(" ", "")
    keyword = (script or {}).get("keyword", "").strip()
    raw_title = ((script or {}).get("title") or keyword or "AI财经热点").strip()
    # 标题默认只用脚本问句（与成片一致）；品牌放简介区即可，标题带品牌易触发平台限流/封禁
    prefix = _env("DOUYIN_TITLE_PREFIX", "0").lower()
    if prefix in ("1", "true", "yes", "on") and brand and brand not in raw_title:
        title = f"【{brand}】{raw_title}"
    else:
        title = raw_title

    topic_kw = _normalize_publish_tags(script)

    desc = _build_publish_desc(script, raw_title, brand)

    return {
        "title": title[:100],
        "desc": desc[:1000],
        "tags": ",".join(topic_kw),
    }
