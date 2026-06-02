#!/usr/bin/env python3
"""每日自动选题：热点候选 → 问句话题线索 → 按方向配额选出 N 条。

取代旧流程「先给每篇文章打 0-100 分再挑文章」；与 ./make-topics.sh / A-B 实验一致：
每条先定 title_hint，再 Exa 搜文、深读、改编。
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime, timezone

import sys

import categories
from batch_aivideo import duplicate_topic_reason, filter_duplicate_topics, history_recent_topics
from paths import ROOT
from research import extract_json, find_articles, load_env, text_model
from text_client import chat_complete

DIRECTION_ORDER = ("astock", "ai", "hkus")
DIRECTION_LABEL = {"astock": "A股", "ai": "AI", "hkus": "港美股"}

_ASTOCK_KW = re.compile(
    r"a股|涨停|跌停|龙虎榜|游资|科创板|创业板|北向|北交所|沪深|沪指|深成指|创业板指|"
    r"科创50|连板|妖股|人气股|打板|涨停板|主力资金|两市|沪市|深市|题材股|概念股|集合竞价",
    re.I,
)
_HKUS_KW = re.compile(
    r"美股|港股|中概|纳斯达克|纳指|道指|标普|nasdaq|nyse|s&p|hong kong|hkex|"
    r"七姐妹|magnificent|wall street|华尔街",
    re.I,
)

# 默认方向占比（可被 AIVIDEO_DIR_RATIO 覆盖，三项之和不必为 1，会归一化）
DEFAULT_DIR_RATIO = {"astock": 0.55, "ai": 0.25, "hkus": 0.20}
DEFAULT_ASTOCK_MIN_RATIO = 0.5  # A股条数须满足 count/target > 此值（默认 >50%）

PROPOSE_SYSTEM = """你是抖音栏目「AI财知道」的每日选题编辑。输入是近几天 AI/财经/A股 热点候选（标题+摘要）。

每条会走：按线索搜文 → 深读 → 改编成 3-4 页短视频。**Hook-First：先定炸裂冷开场，再定问句标题。**

要求：
1. title_hint：中文 8-28 字，搜索友好问句（为什么/意味着什么/到底…）。
2. cold_open：12-28 字，**必须一句话说完**。**先跟普通人生活挂钩**，再抛反差/数字/反问；禁止「今天讲…」和纯术语开场（路人 3 秒听不懂就失败）。
   - 好：「你手机里的一个小元件，居然能带飞整条涨停链」「买菜发现鸡蛋又涨了？背后可能是这种原料」
   - 差：「MLCC概念突然集体涨停」「CPO供给瓶颈加剧」（术语留到正文里由浅入深讲）
3. theme_cluster：概念簇英文 id，同产业链/同题材必须相同。示例：optical_module、ai_chip、ev_auto、macro_rates、consumer_platform；无法归类用 general。
4. angle：10-24 字，本篇唯一视角（如「只讲供给瓶颈，不讲股价」）。
5. 优先 48 小时内真实热点；禁止荐股、喊单、股票代码。
6. 与【近期已做标题】【近期概念簇】避免重复；**本批 6-10 条里同一 theme_cluster 最多出现 1 次**；CPO/光模块/800G/硅光等同簇不要换皮重复。
7. direction=astock 不少于【A股最少条数】条，其余 ai / hkus；direction 只能是 astock、ai、hkus。

只输出 JSON，不要 markdown。"""

PROPOSE_USER = """【今天】{today}

【近期已做过的标题（勿重复）】
{recent_topics_json}

【近期已发概念簇（勿重复）】
{recent_clusters_json}

【热点候选（仅作线索，不必绑定某篇 URL）】
{candidates_json}

输出格式：
{{
  "topics": [
    {{
      "direction": "astock|ai|hkus",
      "category": "astock|ai|hkus",
      "title_hint": "为什么…/…意味着什么",
      "cold_open": "炸裂一句，12-28字",
      "theme_cluster": "optical_module",
      "angle": "本篇唯一角度",
      "reason": "20-40字，说明新鲜度与可讲性"
    }}
  ]
}}"""


def direction_bucket(cand: dict) -> str:
    st = str(cand.get("source_type") or "")
    cat = str(cand.get("category") or "").lower()
    text = " ".join(
        str(cand.get(k) or "")
        for k in ("title", "question_title", "summary_zh", "summary_en", "thesis", "site")
    ).lower()
    if st == "exa:astock" or cat == "astock" or (_ASTOCK_KW.search(text) and not _HKUS_KW.search(text)):
        return "astock"
    if cat == "ai":
        return "ai"
    if cat in {"earnings", "stock", "finance", "macro"}:
        return "hkus"
    return "hkus" if _HKUS_KW.search(text) else "ai"


def _interleave_by_direction(cands: list[dict]) -> list[dict]:
    buckets: dict[str, list[dict]] = {d: [] for d in DIRECTION_ORDER}
    for c in cands:
        buckets[direction_bucket(c)].append(c)
    out: list[dict] = []
    while any(buckets[d] for d in DIRECTION_ORDER):
        for d in DIRECTION_ORDER:
            if buckets[d]:
                out.append(buckets[d].pop(0))
    return out


def _parse_dir_ratio() -> dict[str, float]:
    """读取 AIVIDEO_DIR_RATIO=0.55,0.25,0.20（astock,ai,hkus），归一化为占比。"""
    raw = os.environ.get("AIVIDEO_DIR_RATIO", "").strip()
    if not raw:
        return dict(DEFAULT_DIR_RATIO)
    parts = [p.strip() for p in re.split(r"[,，\s]+", raw) if p.strip()]
    if len(parts) != 3:
        return dict(DEFAULT_DIR_RATIO)
    weights: dict[str, float] = {}
    for key, part in zip(DIRECTION_ORDER, parts):
        try:
            weights[key] = max(0.0, float(part))
        except ValueError:
            return dict(DEFAULT_DIR_RATIO)
    total = sum(weights.values())
    if total <= 0:
        return dict(DEFAULT_DIR_RATIO)
    return {k: weights[k] / total for k in DIRECTION_ORDER}


def _astock_min_ratio() -> float:
    try:
        return max(0.0, min(1.0, float(os.environ.get("AIVIDEO_ASTOCK_MIN_RATIO", str(DEFAULT_ASTOCK_MIN_RATIO)))))
    except ValueError:
        return DEFAULT_ASTOCK_MIN_RATIO


def min_astock_slots(target: int, min_ratio: float | None = None) -> int:
    """满足 count/target > min_ratio 所需的最少 A股 条数（默认 >50% → 3 条里至少 2 条）。"""
    if target <= 0:
        return 0
    r = DEFAULT_ASTOCK_MIN_RATIO if min_ratio is None else min_ratio
    if r <= 0:
        return 0
    # 最小整数 n 使得 n/target > r
    n = math.floor(target * r) + 1
    if n / target <= r:
        n += 1
    return min(target, max(1, n))


def direction_quotas(target: int, present: list[str] | None = None) -> dict[str, int]:
    """按 AIVIDEO_DIR_RATIO 分配条数，并强制 A股条数 > AIVIDEO_ASTOCK_MIN_RATIO × target。"""
    _ = present  # 计划配额不随「本次 Opus 是否提到某方向」缩水，选题阶段再兜底
    quotas = {d: 0 for d in DIRECTION_ORDER}
    if target <= 0:
        return quotas

    ratios = _parse_dir_ratio()
    min_astock = min_astock_slots(target, _astock_min_ratio())

    # 最大余数法：先按占比取整，再把余数分给小数部分最大的方向
    raw = {d: target * ratios[d] for d in DIRECTION_ORDER}
    quotas = {d: int(math.floor(raw[d])) for d in DIRECTION_ORDER}
    remainder = target - sum(quotas.values())
    order_by_frac = sorted(DIRECTION_ORDER, key=lambda d: raw[d] - quotas[d], reverse=True)
    for i in range(remainder):
        quotas[order_by_frac[i % len(DIRECTION_ORDER)]] += 1

    # 强制 A股 > min_ratio
    if quotas["astock"] < min_astock:
        need = min_astock - quotas["astock"]
        quotas["astock"] = min_astock
        for d in ("hkus", "ai"):
            take = min(quotas[d], need)
            quotas[d] -= take
            need -= take
            if need <= 0:
                break

    # 总数必须等于 target
    total = sum(quotas.values())
    while total > target:
        for d in ("hkus", "ai"):
            if quotas[d] > 0 and quotas["astock"] >= min_astock:
                quotas[d] -= 1
                total -= 1
                if total == target:
                    break
    while total < target:
        quotas["astock"] += 1
        total += 1

    return quotas


def _quota_brief(target: int, quotas: dict[str, int]) -> str:
    min_r = _astock_min_ratio()
    pct = quotas["astock"] / target * 100 if target else 0
    parts = [f"{DIRECTION_LABEL[d]} {quotas[d]}" for d in DIRECTION_ORDER if quotas.get(d)]
    return (
        f"{'，'.join(parts)}（A股占比 {pct:.0f}%，要求 >{min_r:.0%}）"
    )


def _candidate_view(candidates: list[dict], *, limit: int) -> list[dict]:
    out: list[dict] = []
    for i, c in enumerate(candidates, 1):
        if len(out) >= limit:
            break
        out.append({
            "index": i,
            "direction": direction_bucket(c),
            "title": str(c.get("title") or "")[:140],
            "site": c.get("site"),
            "published_at": c.get("published_at"),
            "summary_zh": str(c.get("summary_zh") or c.get("thesis") or "")[:200],
            "key_facts": [str(x)[:60] for x in (c.get("key_facts") or [])[:3]],
        })
    return out


def propose_topic_hints(
    candidates: list[dict],
    *,
    recent_topics: list[str],
    target: int = 3,
) -> list[dict]:
    """用一次 Opus 调用，从热点候选提炼问句话题线索。"""
    limit = int(os.environ.get("AIVIDEO_PROPOSE_MAX_CANDIDATES", "28"))
    pool = _interleave_by_direction(candidates)[:limit]
    if not pool:
        return []
    min_astock = min_astock_slots(target)
    system = PROPOSE_SYSTEM.replace("【A股最少条数】", str(min_astock))
    print(
        f"  🤖 让 {text_model()} 从 {len(pool)} 条热点提炼问句话题线索"
        f"（目标 {target} 条，至少 {min_astock} 条 A股）…"
    )
    try:
        from theme_clusters import recent_cluster_summary

        recent_clusters = recent_cluster_summary()
    except Exception:  # noqa: BLE001
        recent_clusters = []

    raw = chat_complete(
        system=system,
        user=PROPOSE_USER.format(
            today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            recent_topics_json=json.dumps(recent_topics or [], ensure_ascii=False, indent=2),
            recent_clusters_json=json.dumps(recent_clusters or [], ensure_ascii=False, indent=2),
            candidates_json=json.dumps(_candidate_view(pool, limit=limit), ensure_ascii=False, indent=2),
        ),
        max_tokens=3500,
        response_format_json=True,
    )
    data = extract_json(raw)
    rows = data.get("topics") or []
    proposed: list[dict] = []
    for row in rows:
        hint = str(row.get("title_hint") or "").strip()
        if not hint or len(hint) < 6:
            continue
        direction = str(row.get("direction") or "ai").strip().lower()
        if direction not in DIRECTION_ORDER:
            direction = direction_bucket({"title": hint, "category": row.get("category")})
        cat = categories.normalize_category(row.get("category")) or direction
        if cat not in DIRECTION_ORDER:
            cat = direction
        cold_open = str(row.get("cold_open") or "").strip()
        angle = str(row.get("angle") or "").strip()
        theme_cluster = str(row.get("theme_cluster") or "").strip()
        try:
            from theme_clusters import infer_theme_cluster

            if not theme_cluster:
                theme_cluster = infer_theme_cluster(hint, cold_open, angle)
        except Exception:  # noqa: BLE001
            theme_cluster = theme_cluster or "general"
        proposed.append({
            "direction": direction,
            "category": cat,
            "title_hint": hint,
            "cold_open": cold_open,
            "theme_cluster": theme_cluster,
            "angle": angle,
            "reason": str(row.get("reason") or "").strip(),
        })
    return proposed


def _topic_as_cand(topic: dict) -> dict:
    return {
        "title": topic.get("title_hint"),
        "question_title": topic.get("title_hint"),
        "title_hint": topic.get("title_hint"),
        "cold_open": topic.get("cold_open"),
        "theme_cluster": topic.get("theme_cluster"),
        "angle": topic.get("angle"),
        "summary_zh": topic.get("reason") or topic.get("title_hint"),
    }


def select_daily_topics(
    proposed: list[dict],
    *,
    target: int,
    recent_topics: list[str] | None = None,
) -> list[dict]:
    """按方向配额选出 target 条，本地去重。"""
    from batch_aivideo import recent_history

    recent_items = recent_history()
    target = max(1, target)
    quotas = direction_quotas(target)
    selected: list[dict] = []
    picked_dirs: dict[str, int] = {d: 0 for d in DIRECTION_ORDER}
    used_hints: set[str] = set()
    used_clusters: dict[str, int] = {}
    dir_rank = {"astock": 0, "ai": 1, "hkus": 2}
    proposed_sorted = sorted(
        proposed,
        key=lambda p: (dir_rank.get(str(p.get("direction") or "ai"), 9),),
    )

    def _try_pick(row: dict, *, respect_quota: bool) -> bool:
        if len(selected) >= target:
            return False
        hint = str(row.get("title_hint") or "").strip()
        if not hint or hint in used_hints:
            return False
        d = row.get("direction", "ai")
        if respect_quota and picked_dirs.get(d, 0) >= quotas.get(d, 0):
            return False
        reason = duplicate_topic_reason(
            _topic_as_cand(row), recent_items, extra_cluster_counts=used_clusters,
        )
        if reason:
            print(f"  ↯ 话题去重：{hint}（{reason}）")
            return False
        cluster = str(row.get("theme_cluster") or "general").strip()
        if cluster != "general" and used_clusters.get(cluster, 0) >= 1:
            print(f"  ↯ 本批概念簇去重：{hint}（簇 {cluster} 本批已选）")
            return False
        for prev in selected:
            if _hints_too_similar(hint, str(prev.get("title_hint") or "")):
                print(f"  ↯ 本批去重：{hint} ≈ {prev.get('title_hint')}")
                return False
        used_hints.add(hint)
        if cluster != "general":
            used_clusters[cluster] = used_clusters.get(cluster, 0) + 1
        picked_dirs[d] = picked_dirs.get(d, 0) + 1
        selected.append({
            "index": len(selected) + 1,
            "raw": hint,
            "title_hint": hint,
            "cold_open": row.get("cold_open"),
            "theme_cluster": cluster,
            "angle": row.get("angle"),
            "provided_content": None,
            "category": row.get("category") or d,
            "direction": d,
            "reason": row.get("reason"),
        })
        return True

    # 阶段1：先按配额选（A股 排在前，优先占满 A股 席位）
    for row in proposed_sorted:
        if len(selected) >= target:
            break
        _try_pick(row, respect_quota=True)
    if len(selected) < target:
        for row in proposed_sorted:
            if len(selected) >= target:
                break
            _try_pick(row, respect_quota=False)
    return selected


def _hints_too_similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    # 简单字符重叠：过长重复子串视为同题
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 8 and short in long


def discover_daily_topics(*, days: int, target: int) -> list[dict]:
    """完整选题：拉热点 → 提炼话题 → 配额选出 target 条。"""
    load_env()
    exclude: list[str] = []
    try:
        from batch_aivideo import history_exclude_urls

        exclude = history_exclude_urls()
    except Exception:  # noqa: BLE001
        pass
    recent_topics = history_recent_topics(limit=80)

    print(f"\n=== 每日选题：近 {days} 天热点 → 问句话题（目标 {target} 条）===")
    candidates, _ = find_articles(
        days=days,
        exclude_urls=exclude,
        recent_topics=recent_topics,
        source="exa",
    )
    candidates = filter_duplicate_topics(candidates)
    if not candidates:
        print("没有可用热点候选。")
        return []

    pre = {d: 0 for d in DIRECTION_ORDER}
    for c in candidates:
        pre[direction_bucket(c)] += 1
    print(f"  候选方向分布：A股 {pre['astock']}，AI {pre['ai']}，港美股 {pre['hkus']}")

    if os.environ.get("AIVIDEO_DIR_QUOTA", "").strip():
        print(
            "  ⚠️  已弃用 AIVIDEO_DIR_QUOTA，请改用 AIVIDEO_DIR_RATIO + AIVIDEO_ASTOCK_MIN_RATIO",
            file=sys.stderr,
        )

    plan = direction_quotas(target)
    print(f"  本轮计划配额：{_quota_brief(target, plan)}")

    proposed = propose_topic_hints(candidates, recent_topics=recent_topics, target=target)
    if not proposed:
        print("未能提炼出话题线索。")
        return []

    print(f"\n提炼出 {len(proposed)} 条话题线索：")
    for i, p in enumerate(proposed, 1):
        tag = DIRECTION_LABEL.get(p.get("direction", ""), "?")
        co = p.get("cold_open") or ""
        cl = p.get("theme_cluster") or ""
        print(f"  {i}. [{tag}] {p.get('title_hint')}")
        if co:
            print(f"      冷开场: {co}")
        if cl:
            print(f"      概念簇: {cl} — {p.get('reason', '')}")
        else:
            print(f"      — {p.get('reason', '')}")

    selected = select_daily_topics(proposed, target=target, recent_topics=recent_topics)
    made = {d: 0 for d in DIRECTION_ORDER}
    for s in selected:
        made[s.get("direction", "ai")] = made.get(s.get("direction", "ai"), 0) + 1
    actual_n = len(selected) or 1
    print(f"\n选定 {len(selected)}/{target} 条（实际 {_quota_brief(actual_n, made)}）：")
    for t in selected:
        tag = DIRECTION_LABEL.get(t.get("direction", ""), "?")
        co = t.get("cold_open") or ""
        extra = f" | 冷开场: {co}" if co else ""
        print(f"  ✓ [{tag}] {t.get('title_hint')}{extra}")

    report = ROOT / "logs" / "daily_topics_last.json"
    report.write_text(
        json.dumps(
            {
                "days": days,
                "target": target,
                "proposed": proposed,
                "selected": selected,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return selected
