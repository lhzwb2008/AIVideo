#!/usr/bin/env python3
"""每日自动选题：热点候选 → 问句话题线索 → 按方向配额选出 N 条。

取代旧流程「先给每篇文章打 0-100 分再挑文章」；与 ./make-topics.sh / A-B 实验一致：
每条先定 title_hint，再 Exa 搜文、深读、改编。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

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

DIRECTION_BASE_QUOTA = {"astock": 1, "ai": 1, "hkus": 1}

PROPOSE_SYSTEM = """你是抖音栏目「AI财知道」的每日选题编辑。输入是近几天 AI/财经/A股 热点候选（标题+摘要），你的任务是提炼「话题线索」title_hint，而不是挑选哪篇文章。

每条 title_hint 会走：按线索搜最新文章 → 深读 → 改编成 3-4 页「为什么/是什么/意味着什么」问答短视频。

要求：
1. title_hint 用中文，8-28 字，适合做成搜索友好问句（为什么/是什么/意味着什么/到底…）。
2. 优先 48 小时内真实热点；A股 爆点（涨停潮/题材/龙虎榜/业绩/IPO）可大胆写，但禁止荐股、喊单、股票代码。
3. 与【近期已做标题】避免同一公司、同一事件、同一财报重复。
4. 输出 6-8 条，尽量覆盖 astock / ai / hkus 三个方向（每方向至少 1 条）。
5. direction 只能是 astock、ai、hkus；category 与 direction 一致（astock→astock, ai→ai, hkus→hkus）。

只输出 JSON，不要 markdown。"""

PROPOSE_USER = """【今天】{today}

【近期已做过的标题（勿重复）】
{recent_topics_json}

【热点候选（仅作线索，不必绑定某篇 URL）】
{candidates_json}

输出格式：
{{
  "topics": [
    {{
      "direction": "astock|ai|hkus",
      "category": "astock|ai|hkus",
      "title_hint": "为什么…/…意味着什么",
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


def _base_quota() -> dict[str, int]:
    raw = os.environ.get("AIVIDEO_DIR_QUOTA", "").strip()
    if not raw:
        return dict(DIRECTION_BASE_QUOTA)
    base = {d: 0 for d in DIRECTION_ORDER}
    for part in raw.split(","):
        if ":" not in part:
            continue
        k, _, v = part.partition(":")
        k = k.strip()
        if k in base:
            try:
                base[k] = max(0, int(v.strip()))
            except ValueError:
                pass
    return base if any(base.values()) else dict(DIRECTION_BASE_QUOTA)


def direction_quotas(target: int, present: list[str]) -> dict[str, int]:
    order = [d for d in DIRECTION_ORDER if d in present]
    quotas = {d: 0 for d in DIRECTION_ORDER}
    if not order or target <= 0:
        return quotas
    n = len(order)
    base = _base_quota()
    remaining = target
    if target >= n:
        for d in order:
            quotas[d] = 1
        remaining -= n
    while remaining > 0:
        progressed = False
        for d in order:
            if remaining <= 0:
                break
            if quotas[d] < base.get(d, 0):
                quotas[d] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    while remaining > 0:
        quotas[order[0]] += 1
        remaining -= 1
    return quotas


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
) -> list[dict]:
    """用一次 Opus 调用，从热点候选提炼问句话题线索。"""
    limit = int(os.environ.get("AIVIDEO_PROPOSE_MAX_CANDIDATES", "28"))
    pool = _interleave_by_direction(candidates)[:limit]
    if not pool:
        return []
    print(f"  🤖 让 {text_model()} 从 {len(pool)} 条热点提炼问句话题线索…")
    raw = chat_complete(
        system=PROPOSE_SYSTEM,
        user=PROPOSE_USER.format(
            today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            recent_topics_json=json.dumps(recent_topics or [], ensure_ascii=False, indent=2),
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
        proposed.append({
            "direction": direction,
            "category": cat,
            "title_hint": hint,
            "reason": str(row.get("reason") or "").strip(),
        })
    return proposed


def _topic_as_cand(topic: dict) -> dict:
    return {
        "title": topic.get("title_hint"),
        "question_title": topic.get("title_hint"),
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
    present = [d for d in DIRECTION_ORDER if any(p.get("direction") == d for p in proposed)]
    quotas = direction_quotas(target, present or list(DIRECTION_ORDER))
    selected: list[dict] = []
    picked_dirs: dict[str, int] = {d: 0 for d in DIRECTION_ORDER}
    used_hints: set[str] = set()

    def _try_pick(row: dict, *, respect_quota: bool) -> bool:
        if len(selected) >= target:
            return False
        hint = str(row.get("title_hint") or "").strip()
        if not hint or hint in used_hints:
            return False
        d = row.get("direction", "ai")
        if respect_quota and picked_dirs.get(d, 0) >= quotas.get(d, 0):
            return False
        reason = duplicate_topic_reason(_topic_as_cand(row), recent_items)
        if reason:
            print(f"  ↯ 话题去重：{hint}（{reason}）")
            return False
        for prev in selected:
            if _hints_too_similar(hint, str(prev.get("title_hint") or "")):
                print(f"  ↯ 本批去重：{hint} ≈ {prev.get('title_hint')}")
                return False
        used_hints.add(hint)
        picked_dirs[d] = picked_dirs.get(d, 0) + 1
        selected.append({
            "index": len(selected) + 1,
            "raw": hint,
            "title_hint": hint,
            "provided_content": None,
            "category": row.get("category") or d,
            "direction": d,
            "reason": row.get("reason"),
        })
        return True

    for row in proposed:
        if len(selected) >= target:
            break
        _try_pick(row, respect_quota=True)
    if len(selected) < target:
        for row in proposed:
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

    proposed = propose_topic_hints(candidates, recent_topics=recent_topics)
    if not proposed:
        print("未能提炼出话题线索。")
        return []

    print(f"\n提炼出 {len(proposed)} 条话题线索：")
    for i, p in enumerate(proposed, 1):
        tag = DIRECTION_LABEL.get(p.get("direction", ""), "?")
        print(f"  {i}. [{tag}] {p.get('title_hint')} — {p.get('reason', '')}")

    selected = select_daily_topics(proposed, target=target, recent_topics=recent_topics)
    quotas = direction_quotas(target, [d for d in DIRECTION_ORDER if any(s.get("direction") == d for s in selected)])
    quota_brief = "，".join(
        f"{DIRECTION_LABEL[d]} {quotas[d]}" for d in DIRECTION_ORDER if quotas.get(d)
    )
    print(f"\n选定 {len(selected)}/{target} 条（配额 {quota_brief}）：")
    for t in selected:
        tag = DIRECTION_LABEL.get(t.get("direction", ""), "?")
        print(f"  ✓ [{tag}] {t.get('title_hint')}")

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
