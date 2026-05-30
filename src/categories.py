#!/usr/bin/env python3
"""子栏目（频道）定义：A股 / 港美股 / AI资讯 / 量化。

都是「AI财知道」主账号的一部分，只是用不同的主题色 + 角标后缀做视觉区分。
- accent：徽标底色 / 封面色块底色（RGB），需保证黑字可读，所以选中高明度色。
- label：角标后缀，展示成「AI财知道 · A股」。
- aliases：从话题标签 [xxx] 里识别该栏目的别名。

量化栏目不改选题逻辑，内容由人工用 make-topics 指定（在话题前加 [量化] 标签即可）。
"""

from __future__ import annotations

import re

# 默认主题色（未归类时用，沿用原黄色高亮）
DEFAULT_ACCENT = (254, 224, 71)

CATEGORIES: dict[str, dict] = {
    "astock": {
        "label": "A股",
        "accent": (244, 124, 109),   # 暖红
        "aliases": ("a股", "astock", "沪深", "国内股市"),
    },
    "hkus": {
        "label": "港美股",
        "accent": (118, 169, 235),   # 蓝
        "aliases": ("港美股", "美股", "港股", "中概", "中概股", "hkus", "海外"),
    },
    "ai": {
        "label": "AI资讯",
        "accent": (183, 148, 244),   # 紫
        "aliases": ("ai", "ai资讯", "ai咨询", "ai资讯", "人工智能", "大模型", "科技"),
    },
    "quant": {
        "label": "量化",
        "accent": (122, 201, 152),   # 绿
        "aliases": ("量化", "quant", "策略", "因子"),
    },
    "basic": {
        "label": "基础",
        "accent": (109, 196, 199),   # 青
        "aliases": ("基础", "基础知识", "basic", "入门", "科普", "扫盲", "名词解释"),
    },
}


def normalize_category(value: str | None) -> str | None:
    """把人写的栏目名/标签归一到内部 key（astock/hkus/ai/quant）。"""
    if not value:
        return None
    v = str(value).strip().strip("[]【】#").lower()
    if not v:
        return None
    if v in CATEGORIES:
        return v
    for key, cfg in CATEGORIES.items():
        if v == key:
            return key
        for alias in cfg["aliases"]:
            if v == alias.lower():
                return key
    return None


# ============================================================
# 自动检测：根据脚本 hashtags / 标题 / 关键词判断归属栏目
# ============================================================
_ASTOCK_SIGNAL = (
    "a股", "涨停", "跌停", "龙虎榜", "游资", "科创板", "创业板", "北向", "北交所",
    "沪深", "沪指", "深成指", "创业板指", "科创50", "连板", "妖股", "人气股",
    "打板", "主力资金", "两市", "沪市", "深市", "题材股", "概念股", "深股通", "沪股通",
)
_HKUS_SIGNAL = (
    "美股", "港股", "中概", "中概股", "纳斯达克", "纳指", "标普", "道指", "恒生", "恒指",
    "英伟达", "苹果", "微软", "特斯拉", "谷歌", "亚马逊", "meta", "台积电",
)
# AI / 科技纯资讯信号（与具体股市行情无关时归 AI资讯）
_AI_SIGNAL = (
    "大模型", "gpt", "claude", "opus", "gemini", "llm", "agent", "智能体",
    "openai", "anthropic", "deepseek", "算法", "人工智能", "机器学习", "推理模型",
)
# 基础 / 科普信号：解释一个通用财经概念或原理，而非某条具体行情/事件。
# 仅作兜底（优先级最低），命中说明这是「名词/原理科普」类内容。
_BASIC_SIGNAL = (
    "是什么", "什么是", "怎么算", "怎么看", "如何计算", "原理", "定律", "定理",
    "公式", "名词解释", "扫盲", "入门", "基础知识", "科普", "通俗解释",
    "市盈率", "市净率", "市销率", "毛利率", "净利率", "净资产", "现金流",
    "估值", "复利", "通胀", "通缩", "加息", "降息", "k线", "均线", "成交量",
    "牛市", "熊市", "做空", "做多", "杠杆", "止损", "分红", "股息",
)


def _script_blob(script: dict | None) -> str:
    parts: list[str] = [
        str((script or {}).get("title") or ""),
        str((script or {}).get("keyword") or ""),
    ]
    for t in (script or {}).get("hashtags") or []:
        parts.append(str(t))
    for slide in (script or {}).get("slides") or []:
        if isinstance(slide, dict):
            parts.append(str(slide.get("headline") or ""))
    return " ".join(parts).lower()


def detect_category(script: dict | None) -> str | None:
    """自动判断脚本归属的子栏目。判断不出时返回 None（用默认主题）。

    优先级：A股信号 > 港美股信号 > 纯 AI 资讯 > 基础/科普（兜底）。
    """
    blob = _script_blob(script)
    if not blob:
        return None
    if any(sig in blob for sig in _ASTOCK_SIGNAL):
        return "astock"
    if any(sig in blob for sig in _HKUS_SIGNAL):
        return "hkus"
    if any(sig in blob for sig in _AI_SIGNAL):
        return "ai"
    if any(sig in blob for sig in _BASIC_SIGNAL):
        return "basic"
    return None


def resolve_category(script: dict | None, explicit: str | None = None) -> str | None:
    """显式指定优先（量化等人工栏目），否则自动检测。"""
    cat = normalize_category(explicit)
    if cat:
        return cat
    if script and script.get("category"):
        cat = normalize_category(script.get("category"))
        if cat:
            return cat
    return detect_category(script)


def accent_color(category: str | None) -> tuple[int, int, int]:
    cfg = CATEGORIES.get(category or "")
    return tuple(cfg["accent"]) if cfg else DEFAULT_ACCENT


def label_of(category: str | None) -> str:
    cfg = CATEGORIES.get(category or "")
    return cfg["label"] if cfg else ""


# ============================================================
# 从话题文字里解析栏目标签，如 "[量化] 多因子选股是什么"
# ============================================================
_TAG_RE = re.compile(r"^\s*[【\[]([^】\]]{1,8})[】\]]\s*")


def extract_category_tag(segment: str) -> tuple[str | None, str]:
    """从一段话题文字开头解析 [栏目] 标签，返回 (category_key, 去掉标签后的文字)。

    只在标签能映射到已知栏目时才剥离；否则原样返回。
    """
    m = _TAG_RE.match(segment or "")
    if not m:
        return None, segment
    cat = normalize_category(m.group(1))
    if not cat:
        return None, segment
    return cat, segment[m.end():].strip()
