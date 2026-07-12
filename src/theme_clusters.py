"""概念簇：选题去重（同产业链/同题材换皮）与 history 联动。

支持一天多次执行 make-and-publish.sh 1：每次做完写入 theme_cluster，
下次选题会避开近 N 天已发簇。

科普（edu_*）额外用「概念指纹」做硬去重：同一概念换皮标题也算重复。
"""

from __future__ import annotations

import os
import re
from typing import Iterable

# cluster_id -> 匹配词（小写）；先匹配先得
CLUSTER_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "optical_module",
        (
            "cpo", "光模块", "光通信", "800g", "1.6t", "硅光", "光器件", "光互联",
            "中际旭创", "新易盛", "天孚通信", "剑桥科技", "源杰科技", "光迅",
        ),
    ),
    (
        "ai_chip",
        (
            "英伟达", "nvidia", "gpu", "算力", "hbm", "ai芯片", "ai 芯片", "半导体设备",
            "台积电", "tsmc", "asml", "先进封装", "cowos",
        ),
    ),
    (
        "ev_auto",
        ("新能源车", "电动车", "特斯拉", "tesla", "比亚迪", "蔚来", "小鹏", "理想汽车", "锂电池"),
    ),
    (
        "macro_rates",
        ("降息", "加息", "美联储", "fed", "cpi", "ppi", "国债", "收益率", "央行"),
    ),
    (
        "consumer_platform",
        ("拼多多", "pdd", "阿里", "淘宝", "京东", "美团", "抖音电商", "直播带货"),
    ),
]

# 科普概念指纹：同一概念换皮标题也算重复（长词优先匹配）
# (concept_id, aliases) — aliases 按长度降序匹配，避免短词误伤
EDU_CONCEPT_ALIASES: list[tuple[str, tuple[str, ...]]] = [
    ("edu_cpi", ("消费者物价指数", "居民消费价格指数", "cpi指数", "cpi", "通货膨胀", "通胀")),
    ("edu_ppi", ("生产者物价指数", "工业生产者出厂价格指数", "ppi指数", "ppi")),
    ("edu_dcf", ("现金流折现", "折现现金流", "自由现金流折现", "dcf估值", "dcf模型", "dcf")),
    ("edu_fcf", ("自由现金流", "fcf")),
    ("edu_max_drawdown", ("最大回撤", "maxdrawdown", "max drawdown")),
    ("edu_sharpe", ("夏普比率", "夏普指数", "sharpe", "夏普")),
    ("edu_volatility", ("波动率", "波动性", "volatility")),
    ("edu_pe", ("市盈率", "pe估值", "pe比率", "市盈")),
    ("edu_pb", ("市净率", "pb估值", "pb比率", "市净")),
    ("edu_ps", ("市销率", "ps估值", "市销")),
    ("edu_roe", ("净资产收益率", "roe")),
    ("edu_roa", ("总资产收益率", "roa")),
    ("edu_eps", ("每股收益", "eps")),
    ("edu_dividend", ("股息率", "分红收益率", "分红率")),
    ("edu_beta", ("贝塔系数", "beta系数", "β系数")),
    ("edu_nav", ("净资产", "净值")),
    ("edu_etf", ("交易型开放式指数基金", "etf基金", "etf")),
    ("edu_pevc", ("私募股权", "风险投资", "vc/pe", "pe/vc")),
    ("edu_bond_yield", ("国债收益率", "债券收益率", "十年期国债")),
    ("edu_money_supply", ("广义货币", "狭义货币", "m2增速", "m2", "m1")),
    ("edu_interest_rate", ("基准利率", "贷款利率", "存款利率", "lpr")),
    ("edu_margin", ("融资融券", "两融余额", "两融")),
    ("edu_northbound", ("北向资金", "陆股通", "沪股通", "深股通")),
    ("edu_southbound", ("南向资金", "南向通", "港股通")),
    ("edu_essential_drug", ("基药目录", "基本药物目录", "基本药物", "基药")),
    ("edu_ipo", ("首次公开募股", "ipo发行", "ipo")),
    ("edu_buyback", ("股票回购", "股份回购", "回购注销")),
    ("edu_short_selling", ("卖空机制", "融券做空", "做空")),
    ("edu_circuit_breaker", ("熔断机制", "涨跌停板", "涨跌停")),
    ("edu_alpha_beta", ("超额收益", "alpha", "α收益")),
    ("edu_backtest", ("回测框架", "策略回测", "回测")),
    ("edu_factor", ("多因子模型", "因子投资", "量化因子", "因子")),
    ("edu_macd", ("macd指标", "macd")),
    ("edu_kdj", ("kdj指标", "kdj")),
    ("edu_rsi", ("相对强弱指标", "rsi指标", "rsi")),
    ("edu_moving_avg", ("移动平均线", "均线系统", "ma均线", "均线")),
]

_DEFAULT_CLUSTER = "general"

_FILLER_RE = re.compile(
    r"(是什么|是啥|怎么算|怎么看|怎么选|如何计算|如何理解|有什么区别|有啥区别|"
    r"有什么不一样|有啥不一样|是一回事吗|意味着什么|跟你有什么关系|跟咱们有啥关系|"
    r"入门|科普|一文搞懂|三分钟|3分钟|大白话|通俗|讲解|逻辑是什么|"
    r"为什么要|为啥要|一起看|更安全的策略)"
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _norm_compact(text: str) -> str:
    t = re.sub(r"\s+", "", (text or "").lower())
    return re.sub(r"[？?！!。，、：:；;「」\"'（）()【】\[\]·\-—/\\]", "", t)


def edu_dedup_days() -> int:
    """科普概念去重窗口；默认 3650≈十年，近似永不重复。"""
    raw = os.environ.get("AIVIDEO_EDU_DEDUP_DAYS", "3650").strip()
    try:
        return max(30, int(raw))
    except ValueError:
        return 3650


def infer_edu_concept(*texts: str) -> str:
    """从标题推断稳定科普概念 id；未命中返回空串。"""
    hay = _norm_compact(" ".join(t for t in texts if t))
    if not hay:
        return ""
    best_id = ""
    best_len = 0
    for concept_id, aliases in EDU_CONCEPT_ALIASES:
        for alias in aliases:
            a = _norm_compact(alias)
            if not a:
                continue
            if a in hay and len(a) > best_len:
                best_id = concept_id
                best_len = len(a)
    return best_id


def infer_theme_cluster(*texts: str) -> str:
    """从标题/钩子/角度等推断概念簇。

    新闻题材关键词优先；未命中时再尝试科普概念指纹。
    """
    hay = _norm(" ".join(t for t in texts if t))
    if not hay:
        return _DEFAULT_CLUSTER
    for cluster_id, keywords in CLUSTER_KEYWORDS:
        for kw in keywords:
            if kw.lower() in hay:
                return cluster_id
    edu = infer_edu_concept(*texts)
    if edu:
        return edu
    return _DEFAULT_CLUSTER


def theme_dedup_days() -> int:
    raw = os.environ.get("AIVIDEO_THEME_DEDUP_DAYS", "7").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 7


def theme_max_per_day() -> int:
    """同一概念簇当天最多允许发几条（跨多次 make-and-publish 累计）。"""
    raw = os.environ.get("AIVIDEO_THEME_MAX_PER_DAY", "1").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _item_cluster(item: dict) -> str:
    c = str(item.get("theme_cluster") or "").strip()
    if c:
        return c
    return infer_theme_cluster(
        str(item.get("title") or ""),
        str(item.get("script_title") or ""),
        str(item.get("cold_open") or ""),
        str(item.get("title_hint") or ""),
    )


def _item_edu_concept(item: dict) -> str:
    explicit = str(item.get("edu_concept") or "").strip()
    if explicit.startswith("edu_"):
        return explicit
    cluster = str(item.get("theme_cluster") or "").strip()
    if cluster.startswith("edu_"):
        return cluster
    return infer_edu_concept(
        str(item.get("script_title") or ""),
        str(item.get("title") or ""),
        str(item.get("title_hint") or ""),
        str(item.get("question_title") or ""),
    )


def clusters_from_items(items: Iterable[dict]) -> dict[str, int]:
    """统计各簇出现次数。"""
    counts: dict[str, int] = {}
    for item in items:
        cid = _item_cluster(item)
        if cid == _DEFAULT_CLUSTER:
            continue
        counts[cid] = counts.get(cid, 0) + 1
    return counts


def edu_concepts_from_titles(titles: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for t in titles:
        cid = infer_edu_concept(t)
        if cid:
            out.add(cid)
    return out


def edu_concepts_from_items(items: Iterable[dict]) -> set[str]:
    out: set[str] = set()
    for item in items:
        cid = _item_edu_concept(item)
        if cid:
            out.add(cid)
    return out


def titles_concept_overlap(a: str, b: str) -> bool:
    """两标题是否指向同一科普概念（含换皮）。"""
    ca, cb = infer_edu_concept(a), infer_edu_concept(b)
    if ca and cb and ca == cb:
        return True
    na, nb = _norm_compact(a), _norm_compact(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 4 and shorter in longer:
        return True
    core_a = _FILLER_RE.sub("", na)
    core_b = _FILLER_RE.sub("", nb)
    if len(core_a) >= 3 and core_a == core_b:
        return True
    if len(core_a) >= 4 and len(core_b) >= 4:
        s, l = (core_a, core_b) if len(core_a) <= len(core_b) else (core_b, core_a)
        if s in l:
            return True
    return False


def cluster_duplicate_reason(
    candidate: dict,
    recent_items: list[dict] | None = None,
    *,
    extra_counts: dict[str, int] | None = None,
) -> str:
    """概念簇去重：近 N 天已发 / 本批已选 / 当日超额。

    edu_* 簇使用更长的科普窗口（默认近似永不重复）。
    """
    title = str(
        candidate.get("title_hint")
        or candidate.get("title")
        or candidate.get("suggested_video_title")
        or ""
    )
    cand_cluster = str(candidate.get("theme_cluster") or "").strip()
    if not cand_cluster:
        cand_cluster = infer_theme_cluster(
            title,
            str(candidate.get("cold_open") or ""),
            str(candidate.get("angle") or ""),
        )
    edu_concept = str(candidate.get("edu_concept") or "").strip() or infer_edu_concept(title)
    if edu_concept:
        cand_cluster = edu_concept

    if cand_cluster == _DEFAULT_CLUSTER:
        return ""

    from batch_aivideo import recent_history

    is_edu = cand_cluster.startswith("edu_")
    lookback = edu_dedup_days() if is_edu else theme_dedup_days()
    items = recent_items if recent_items is not None else recent_history(lookback)
    counts = clusters_from_items(items)
    if is_edu:
        for cid in edu_concepts_from_items(items):
            counts[cid] = max(counts.get(cid, 0), 1)
    if extra_counts:
        for k, v in extra_counts.items():
            counts[k] = counts.get(k, 0) + v

    max_allowed = 1 if is_edu else theme_max_per_day()
    used = counts.get(cand_cluster, 0)
    if used >= max_allowed:
        kind = "科普概念" if is_edu else "概念簇"
        return (
            f"{kind}「{cand_cluster}」近 {lookback} 天已发 {used} 条"
            f"（上限 {max_allowed}）"
        )
    return ""


def recent_cluster_summary(items: list[dict] | None = None, *, limit: int = 12) -> list[str]:
    """给选题模型看的近期簇列表。"""
    from batch_aivideo import recent_history

    rows = items if items is not None else recent_history(theme_dedup_days())
    out: list[str] = []
    seen: set[str] = set()
    for x in reversed(rows):
        cid = _item_cluster(x)
        if cid in seen or cid == _DEFAULT_CLUSTER:
            continue
        seen.add(cid)
        title = str(x.get("script_title") or x.get("title") or "").strip()
        out.append(f"{cid}: {title[:40]}")
        if len(out) >= limit:
            break
    return out
