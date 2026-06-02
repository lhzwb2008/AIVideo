"""概念簇：选题去重（同产业链/同题材换皮）与 history 联动。

支持一天多次执行 make-and-publish.sh 1：每次做完写入 theme_cluster，
下次选题会避开近 N 天已发簇。
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
            "台积电", "tsmc", "asml", "先进封装", "coWoS",
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

_DEFAULT_CLUSTER = "general"


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def infer_theme_cluster(*texts: str) -> str:
    """从标题/钩子/角度等推断概念簇。"""
    hay = _norm(" ".join(t for t in texts if t))
    if not hay:
        return _DEFAULT_CLUSTER
    for cluster_id, keywords in CLUSTER_KEYWORDS:
        for kw in keywords:
            if kw.lower() in hay:
                return cluster_id
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


def clusters_from_items(items: Iterable[dict]) -> dict[str, int]:
    """统计各簇出现次数。"""
    counts: dict[str, int] = {}
    for item in items:
        cid = _item_cluster(item)
        if cid == _DEFAULT_CLUSTER:
            continue
        counts[cid] = counts.get(cid, 0) + 1
    return counts


def cluster_duplicate_reason(
    candidate: dict,
    recent_items: list[dict] | None = None,
    *,
    extra_counts: dict[str, int] | None = None,
) -> str:
    """概念簇去重：近 N 天已发 / 本批已选 / 当日超额。"""
    cand_cluster = str(candidate.get("theme_cluster") or "").strip()
    if not cand_cluster:
        cand_cluster = infer_theme_cluster(
            str(candidate.get("title_hint") or candidate.get("title") or ""),
            str(candidate.get("cold_open") or ""),
            str(candidate.get("angle") or ""),
        )
    if cand_cluster == _DEFAULT_CLUSTER:
        return ""

    from batch_aivideo import recent_history

    items = recent_items if recent_items is not None else recent_history(theme_dedup_days())
    counts = clusters_from_items(items)
    if extra_counts:
        for k, v in extra_counts.items():
            counts[k] = counts.get(k, 0) + v

    max_day = theme_max_per_day()
    used = counts.get(cand_cluster, 0)
    if used >= max_day:
        return (
            f"概念簇「{cand_cluster}」近 {theme_dedup_days()} 天已发 {used} 条"
            f"（上限 {max_day}，支持一天多次执行时累计计数）"
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
