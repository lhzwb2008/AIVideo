#!/usr/bin/env python3
"""每日自动选题：热点候选 → 问句话题线索 → 按方向配额选出 N 条。

取代旧流程「先给每篇文章打 0-100 分再挑文章」；与 ./make-topics.sh / A-B 实验一致：
每条先定 title_hint，再 Exa 搜文、深读、改编。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

import sys

import categories
from batch_aivideo import duplicate_topic_reason, filter_duplicate_topics, history_recent_topics
from paths import ROOT
from research import extract_json, find_articles, load_env, text_model
from text_client import chat_complete

DIRECTION_ORDER = ("astock", "sector", "hkus", "ai", "macro")
DIRECTION_LABEL = {
    "astock": "A股个股",
    "sector": "A股板块和大盘",
    "hkus": "港美股个股分析",
    "ai": "AI资讯",
    "macro": "国际金融形势分析",
}

# 固定每日选题顺序：每天最多 5 条，每类 1 条；超过 5 条时从头重新排队。
TOPIC_SLOT_ORDER = DIRECTION_ORDER
TOPIC_SLOT_TO_CATEGORY = {
    "astock": "astock",
    "sector": "astock",
    "hkus": "hkus",
    "ai": "ai",
    "macro": "hkus",
}

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
_SECTOR_KW = re.compile(
    r"板块|大盘|指数|沪指|深成指|创业板指|科创50|北证50|行业|概念|题材|主线|两市|"
    r"成交额|北向|南向|资金流|轮动|风格|领涨|领跌",
    re.I,
)
_MACRO_KW = re.compile(
    r"美联储|fed|降息|加息|利率|通胀|cpi|ppi|非农|就业|美元|美债|国债|汇率|"
    r"央行|欧洲央行|日本央行|油价|黄金|地缘|贸易|关税|全球|国际金融|宏观",
    re.I,
)
_STOCK_CODE_RE = re.compile(
    r"(?<!\d)(?:[0368]\d{5})(?!\d)|\b\d{4,5}\.HK\b|\b[A-Z]{1,5}\b",
    re.I,
)

PROPOSE_SYSTEM = """你是抖音栏目「AI财知道」的每日选题编辑。输入是近几天 AI/财经/A股/港美股/国际宏观 热点候选（标题+摘要）。

每条会走：按线索搜文 → 深读 → 改编成 3-4 页短视频。**Hook-First：先定炸裂冷开场，再定问句标题。**

要求：
1. title_hint：中文 8-28 字，搜索友好问句（为什么/意味着什么/到底…）。
2. cold_open：12-28 字，**必须一句话说完**。**先跟普通人生活挂钩**，再抛反差/数字/反问；禁止「今天讲…」和纯术语开场（路人 3 秒听不懂就失败）。
   - 好：「你手机里的一个小元件，居然能带飞整条涨停链」「买菜发现鸡蛋又涨了？背后可能是这种原料」
   - 差：「MLCC概念突然集体涨停」「CPO供给瓶颈加剧」（术语留到正文里由浅入深讲）
3. theme_cluster：概念簇英文 id，同产业链/同题材必须相同。示例：optical_module、ai_chip、ev_auto、macro_rates、consumer_platform；无法归类用 general。
4. angle：10-24 字，本篇唯一视角（如「只讲供给瓶颈，不讲股价」）。
5. 优先 48 小时内真实热点；禁止荐股、喊单、股票代码。
6. 与【近期已做标题】【近期概念簇】避免重复；**本批同一 theme_cluster 最多出现 1 次**；CPO/光模块/800G/硅光等同簇不要换皮重复。
7. 不再按概率或随机比例选题。请严格围绕【今日固定选题槽位】输出，每个槽位尽量给 1-2 条候选，按槽位顺序排列。
8. direction 只能是 astock、sector、hkus、ai、macro：
   - astock：A股个股
   - sector：A股板块和大盘
   - hkus：港美股个股分析
   - ai：AI资讯
   - macro：国际金融形势分析
9. 个股分析强规则：direction=astock 或 hkus 时，必须给出 entity_name（公司名/常用简称），且 title_hint 必须包含公司名或简称。优先保留强钩子，但标题要像正常中文标题，不要像文件名。推荐格式：「暴涨8倍后突然连续跌停，利通电子怎么了？」、「暴涨8倍的妖股为何突然连续跌停？利通电子6月3日走势分析」、「取消募资又回购12亿，TCL科技释放什么信号？」、「财报后为何大跌，英伟达发生了什么？」。禁止只写“妖股/8倍牛股/某龙头/这家公司”等泛称。
10. 严禁输出任何股票代码，也不要出现买入、卖出、抄底、目标价、荐股、追不追、能不能买等交易引导。

只输出 JSON，不要 markdown。"""

PROPOSE_USER = """【今天】{today}

【近期已做过的标题（勿重复）】
{recent_topics_json}

【近期已发概念簇（勿重复）】
{recent_clusters_json}

【热点候选（仅作线索，不必绑定某篇 URL）】
{candidates_json}

【今日固定选题槽位】
{slots_json}

输出格式：
{{
  "topics": [
    {{
      "direction": "astock|sector|hkus|ai|macro",
      "category": "astock|hkus|ai",
      "entity_name": "个股方向必填，公司名或常用简称；非个股可空",
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
    if _MACRO_KW.search(text) and not _ASTOCK_KW.search(text):
        return "macro"
    if st == "exa:astock" or cat == "astock" or (_ASTOCK_KW.search(text) and not _HKUS_KW.search(text)):
        return "sector" if _SECTOR_KW.search(text) else "astock"
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


def planned_slots(target: int, *, start_offset: int = 0) -> list[str]:
    """按固定顺序排每日选题槽位；超过 5 条时从头重新排。"""
    if target <= 0:
        return []
    return [TOPIC_SLOT_ORDER[(start_offset + i) % len(TOPIC_SLOT_ORDER)] for i in range(target)]


def _today_queue_offset() -> int:
    """当天队列续排：优先接在今天最新一条的槽位后面。"""
    try:
        from batch_aivideo import recent_history
    except Exception:  # noqa: BLE001
        return 0
    now = datetime.now(timezone.utc)
    local_offset = timedelta(hours=8)
    today = (now + local_offset).date()
    today_items: list[dict] = []
    for item in recent_history(days=2):
        raw = str(item.get("made_at") or "").strip()
        if not raw:
            continue
        try:
            ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if (ts.astimezone(timezone.utc) + local_offset).date() == today:
            today_items.append(item)
    if not today_items:
        return 0

    latest = today_items[-1]
    latest_slot = str(latest.get("topic_slot") or latest.get("direction") or "").strip().lower()
    if latest_slot not in TOPIC_SLOT_ORDER:
        cat = str(latest.get("category") or "").strip().lower()
        latest_slot = cat if cat in TOPIC_SLOT_ORDER else direction_bucket(latest)
    if latest_slot in TOPIC_SLOT_ORDER:
        return (TOPIC_SLOT_ORDER.index(latest_slot) + 1) % len(TOPIC_SLOT_ORDER)
    return len(today_items) % len(TOPIC_SLOT_ORDER)


def direction_quotas(
    target: int,
    present: list[str] | None = None,
    *,
    start_offset: int = 0,
) -> dict[str, int]:
    """兼容旧调用：返回固定槽位计数，不再使用概率/比例。"""
    _ = present
    quotas = {d: 0 for d in DIRECTION_ORDER}
    for slot in planned_slots(target, start_offset=start_offset):
        quotas[slot] += 1
    return quotas


def _quota_brief(target: int, quotas: dict[str, int]) -> str:
    parts = [f"{DIRECTION_LABEL[d]} {quotas[d]}" for d in DIRECTION_ORDER if quotas.get(d)]
    return "，".join(parts)


def _slot_view(target: int, *, start_offset: int = 0) -> list[dict]:
    return [
        {
            "order": i + 1,
            "direction": slot,
            "label": DIRECTION_LABEL[slot],
            "category": TOPIC_SLOT_TO_CATEGORY[slot],
        }
        for i, slot in enumerate(planned_slots(target, start_offset=start_offset))
    ]


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


def _contains_stock_code(text: str) -> bool:
    return bool(_STOCK_CODE_RE.search(text or ""))


def _normalize_entity_name(value: str | None) -> str:
    name = str(value or "").strip()
    name = re.sub(r"[（(][^）)]*(?:\d{4,6}|[A-Z]{1,5}\.?HK?)[^）)]*[）)]", "", name).strip()
    return re.sub(r"\s+", "", name)


def _title_with_entity(hint: str, entity: str, direction: str) -> str | None:
    hint = str(hint or "").strip()
    entity = _normalize_entity_name(entity)
    if direction not in {"astock", "hkus"}:
        return hint
    if not entity:
        return None
    if _contains_stock_code(hint) or _contains_stock_code(entity):
        return None
    if entity in hint:
        return hint

    # 个股流量标题优先保留主钩子，把名称放进短副标题，避免削弱标题冲击力。
    if direction == "astock":
        today = f"{datetime.now().month}月{datetime.now().day}日"
        if hint.endswith(("？", "?")):
            return f"{hint}{entity}{today}走势分析"
        suffix = f"，{entity}{today}走势分析"
    else:
        suffix = f"，{entity}事件分析"
    if len(hint) <= 24:
        return f"{hint}{suffix}"

    rewritten = hint
    replacements = (
        r"这家公司",
        r"这只股",
        r"这只个股",
        r"该公司",
        r"某公司",
        r"某龙头",
        r"这家龙头",
        r"这只妖股",
        r"妖股",
        r"牛股",
        r"龙头股",
    )
    for pat in replacements:
        new_title = re.sub(pat, entity, rewritten, count=1)
        if new_title != rewritten:
            return new_title

    m = re.match(r"^(暴涨|大涨|飙涨|狂飙|连涨)([^，。！？?]{1,12})(.*)$", hint)
    if m:
        return f"{entity}为何{m.group(1)}{m.group(2)}{m.group(3)}"

    trimmed = re.sub(r"^[：:，,、\s]+", "", hint).strip()
    if not trimmed:
        return f"{entity}发生了什么？"
    if trimmed.startswith(("为", "因", "凭", "靠", "能", "会")):
        return f"{entity}{trimmed}"
    if trimmed.startswith(("为什么", "为何")):
        return f"{entity}{trimmed}"
    if trimmed.endswith(("？", "?")):
        core = trimmed.rstrip("？?")
        return f"{entity}{core}，关键看什么？"
    return f"{entity}{trimmed}"


def propose_topic_hints(
    candidates: list[dict],
    *,
    recent_topics: list[str],
    target: int = 3,
    start_offset: int = 0,
) -> list[dict]:
    """用一次 Opus 调用，从热点候选提炼问句话题线索。"""
    limit = int(os.environ.get("AIVIDEO_PROPOSE_MAX_CANDIDATES", "28"))
    pool = _interleave_by_direction(candidates)[:limit]
    if not pool:
        return []
    print(
        f"  🤖 让 {text_model()} 从 {len(pool)} 条热点提炼问句话题线索"
        f"（目标 {target} 条，固定顺序："
        f"{_quota_brief(target, direction_quotas(target, start_offset=start_offset))}）…"
    )
    try:
        from theme_clusters import recent_cluster_summary

        recent_clusters = recent_cluster_summary()
    except Exception:  # noqa: BLE001
        recent_clusters = []

    raw = chat_complete(
        system=PROPOSE_SYSTEM,
        user=PROPOSE_USER.format(
            today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            recent_topics_json=json.dumps(recent_topics or [], ensure_ascii=False, indent=2),
            recent_clusters_json=json.dumps(recent_clusters or [], ensure_ascii=False, indent=2),
            candidates_json=json.dumps(_candidate_view(pool, limit=limit), ensure_ascii=False, indent=2),
            slots_json=json.dumps(_slot_view(target, start_offset=start_offset), ensure_ascii=False, indent=2),
        ),
        max_tokens=3500,
        response_format_json=True,
    )
    data = extract_json(raw)
    rows = data.get("topics") or []
    proposed: list[dict] = []
    for row in rows:
        raw_hint = str(row.get("title_hint") or "").strip()
        if not raw_hint or len(raw_hint) < 6:
            continue
        direction = str(row.get("direction") or "ai").strip().lower()
        if direction not in DIRECTION_ORDER:
            direction = direction_bucket({"title": raw_hint, "category": row.get("category")})
        cat = categories.normalize_category(row.get("category")) or TOPIC_SLOT_TO_CATEGORY.get(direction, direction)
        entity_name = _normalize_entity_name(row.get("entity_name"))
        hint = _title_with_entity(raw_hint, entity_name, direction)
        if not hint or _contains_stock_code(hint):
            continue
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
            "entity_name": entity_name,
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
    start_offset: int = 0,
) -> list[dict]:
    """按固定槽位顺序选出 target 条，本地去重。"""
    from batch_aivideo import recent_history

    recent_items = recent_history()
    target = max(1, target)
    slots = planned_slots(target, start_offset=start_offset)
    selected: list[dict] = []
    used_hints: set[str] = set()
    used_clusters: dict[str, int] = {}

    def _try_pick(row: dict, *, slot: str | None) -> bool:
        if len(selected) >= target:
            return False
        hint = str(row.get("title_hint") or "").strip()
        if not hint or hint in used_hints:
            return False
        d = row.get("direction", "ai")
        if slot and d != slot:
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
        selected.append({
            "index": len(selected) + 1,
            "raw": hint,
            "title_hint": hint,
            "entity_name": row.get("entity_name"),
            "cold_open": row.get("cold_open"),
            "theme_cluster": cluster,
            "angle": row.get("angle"),
            "provided_content": None,
            "category": row.get("category") or TOPIC_SLOT_TO_CATEGORY.get(d, d),
            "direction": d,
            "reason": row.get("reason"),
        })
        return True

    # 阶段1：严格按固定槽位顺序取，每个槽位 1 条。
    for slot in slots:
        for row in proposed:
            if _try_pick(row, slot=slot):
                break

    # 阶段2：某槽位素材不足时，为保证执行不中断，按原顺序补足。
    if len(selected) < target:
        for row in proposed:
            if len(selected) >= target:
                break
            _try_pick(row, slot=None)
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
    start_offset = _today_queue_offset()

    print(f"\n=== 每日选题：近 {days} 天热点 → 问句话题（目标 {target} 条）===")
    slots = planned_slots(target, start_offset=start_offset)
    print(f"  今日已完成 {start_offset} 条，队列从第 {start_offset + 1} 类继续")
    print(f"  本轮搜索模板：{_quota_brief(target, direction_quotas(target, start_offset=start_offset))}")
    candidates, _ = find_articles(
        days=days,
        exclude_urls=exclude,
        recent_topics=recent_topics,
        source="exa",
        focus_directions=slots,
    )
    candidates = filter_duplicate_topics(candidates)
    if not candidates:
        print("没有可用热点候选。")
        return []

    pre = {d: 0 for d in DIRECTION_ORDER}
    for c in candidates:
        pre[direction_bucket(c)] += 1
    print(f"  候选方向分布：{_quota_brief(sum(pre.values()), pre)}")

    if os.environ.get("AIVIDEO_DIR_QUOTA", "").strip() or os.environ.get("AIVIDEO_DIR_RATIO", "").strip():
        print(
            "  ⚠️  已弃用 AIVIDEO_DIR_QUOTA/AIVIDEO_DIR_RATIO：当前按五类固定顺序选题",
            file=sys.stderr,
        )

    plan = direction_quotas(target, start_offset=start_offset)
    print(f"  本轮计划配额：{_quota_brief(target, plan)}")

    proposed = propose_topic_hints(
        candidates,
        recent_topics=recent_topics,
        target=target,
        start_offset=start_offset,
    )
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

    selected = select_daily_topics(
        proposed,
        target=target,
        recent_topics=recent_topics,
        start_offset=start_offset,
    )
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
