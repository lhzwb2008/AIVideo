#!/usr/bin/env python3
"""Cursor Cloud Agent 固定五槽位日更：联网调研 → 长文草稿 → Opus 深读 → 短视频改编。

槽位顺序（每天按序，可接昨日进度续排）：
  1. astock_market  — A股大盘报盘与分析
  2. astock_sector  — A股热点板块分析
  3. domestic       — 国内财经新闻分析
  4. ai             — AI 新闻热点分析
  5. world          — 世界财经新闻分析
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from cursor_client import create_agent, create_run, model_id, run_with_stream
from paths import ROOT
from research import deep_read_article, load_env

# 固定每日顺序；超过 5 条时循环
CURSOR_SLOT_ORDER = (
    "astock_market",
    "astock_sector",
    "domestic",
    "ai",
    "world",
)

SLOT_LABEL: dict[str, str] = {
    "astock_market": "A股大盘报盘与分析",
    "astock_sector": "A股热点板块分析",
    "domestic": "国内财经新闻分析",
    "ai": "AI新闻热点分析",
    "world": "世界财经新闻分析",
}

SLOT_TO_CATEGORY: dict[str, str] = {
    "astock_market": "astock",
    "astock_sector": "astock",
    "domestic": "astock",
    "ai": "ai",
    "world": "hkus",
}

_CN_TZ_OFFSET = timedelta(hours=8)
ASTOCK_MARKET_SLOT = "astock_market"
ASTOCK_SECTOR_SLOT = "astock_sector"
OFFDAY_SKIP_SLOTS = frozenset({ASTOCK_MARKET_SLOT, ASTOCK_SECTOR_SLOT})


def china_today() -> date:
    """中国时区（UTC+8）下的日历日期。"""
    return (datetime.now(timezone.utc) + _CN_TZ_OFFSET).date()


def _cn_holiday_dates() -> set[date]:
    """可选：.env 里 AIVIDEO_CN_HOLIDAYS=2026-01-01,2026-02-18 补充法定节假日。"""
    raw = os.environ.get("AIVIDEO_CN_HOLIDAYS", "").strip()
    out: set[date] = set()
    for part in raw.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(date.fromisoformat(part))
        except ValueError:
            continue
    return out


def is_cn_workday(d: date | None = None) -> bool:
    """是否为中国工作日（周一至周五，且不在 AIVIDEO_CN_HOLIDAYS 列表）。"""
    d = d or china_today()
    if d in _cn_holiday_dates():
        return False
    return d.weekday() < 5


def should_skip_astock_market_today() -> bool:
    """非工作日跳过 A股大盘与热点板块槽位；AIVIDEO_FORCE_ASTOCK_MARKET=1 可强制保留大盘。"""
    if os.environ.get("AIVIDEO_SKIP_ASTOCK_MARKET_OFFDAY", "1").strip().lower() in (
        "0",
        "false",
        "no",
    ):
        return False
    if os.environ.get("AIVIDEO_FORCE_ASTOCK_MARKET", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return False
    return not is_cn_workday()


def fixed_market_video_title(d: date | None = None) -> str:
    """第一槽位固定视频/文章标题，如「6月4日A股大盘分析」。"""
    d = d or date.today()
    return f"{d.month}月{d.day}日A股大盘分析"


def astock_market_cold_open(d: date | None = None) -> str:
    d = d or date.today()
    return f"{d.month}月{d.day}日收盘了，3分钟帮你看懂大盘"


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _md_to_date(month: int, day: int) -> date | None:
    """只有「M月D日」时按当前年份推断；若推断出未来日期则回退到去年。"""
    today = date.today()
    d = _safe_date(today.year, month, day)
    if d and d > today:
        d = _safe_date(today.year - 1, month, day)
    return d


def _extract_trading_date(markdown: str) -> date | None:
    """从大盘报盘草稿里解析「最近已收盘交易日」（不是今天）。

    优先级：机器标记 > 明确描述「已收盘/对应…交易日」> 通用年月日（排除被描述为未收盘的今天）。
    """
    if not markdown:
        return None

    # 1) 机器可读标记：交易日：YYYY-MM-DD
    m = re.search(r"交易日[：:]\s*(\d{4})-(\d{1,2})-(\d{1,2})", markdown)
    if m:
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            return d

    # 2) 明确描述「已完整收盘的交易日为 …」/「对应 … 交易日」/「收盘数据对应 …」
    desc_patterns = (
        r"已(?:完整)?收盘的交易日为\s*(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日",
        r"对应\s*(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*(?:交易日|收盘)",
        r"收盘数据(?:主要)?(?:对应|为|来自)\s*(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日",
    )
    for pat in desc_patterns:
        m = re.search(pat, markdown)
        if m:
            year = int(m.group(1)) if m.group(1) else None
            month, day = int(m.group(2)), int(m.group(3))
            d = _safe_date(year, month, day) if year else _md_to_date(month, day)
            if d:
                return d

    # 3) 通用兜底：收集所有「YYYY年M月D日」，排除被描述为「尚未收盘/盘中」的今天
    today = date.today()
    candidates: list[date] = []
    for ym in re.finditer(r"(\d{4})年(\d{1,2})月(\d{1,2})日", markdown):
        d = _safe_date(int(ym.group(1)), int(ym.group(2)), int(ym.group(3)))
        if not d or d > today:
            continue
        # 若该日期紧邻「尚未收盘/未收盘/盘中/早间」等字样，视为今天、跳过
        ctx = markdown[max(0, ym.start() - 10): ym.end() + 16]
        if d == today and re.search(r"尚未收盘|未收盘|盘中|早间|开盘", ctx):
            continue
        candidates.append(d)
    if candidates:
        # 取最近的一个已收盘交易日（最大但 ≤ 今天；优先非今天）
        non_today = [d for d in candidates if d != today]
        pool = non_today or candidates
        return max(pool)
    return None


def market_title_for_date(d: date) -> str:
    return f"{d.month}月{d.day}日A股大盘分析"


def topic_plan_for_slot(slot: str, *, d: date | None = None) -> dict:
    """各槽位写入 _topic_plan，供 Opus 改编时约束形态。"""
    d = d or date.today()
    if slot == "astock_market":
        fixed = fixed_market_video_title(d)
        return {
            "slot": slot,
            "script_mode": "daily_recap",
            "fixed_video_title": fixed,
            "title_hint": fixed,
            "cold_open": astock_market_cold_open(d),
            "angle": "收盘数据报盘+简要解读，不写板块专题",
            "theme_cluster": "astock_daily_recap",
        }
    label = SLOT_LABEL.get(slot, slot)
    return {
        "slot": slot,
        "title_hint": f"{d.isoformat()} {label}",
        "angle": label,
        "theme_cluster": f"cursor_{slot}",
    }


_COMMON_RULES = """
硬性要求（全部槽位通用）：
- 必须联网检索「最新交易日/最近 48 小时」公开报道（财联社、证券时报、新浪财经、东方财富、金融界、华尔街见闻、Reuters、Bloomberg 等），交叉验证
- 禁止编造搜不到的股价、涨跌幅、成交额；不确定写「数据待核实」
- 禁止股票代码、荐股、目标价、买卖建议、仓位建议
- 全文中文 Markdown；文末一行：*免责声明：本文为信息梳理，不构成投资建议。*
- 把完整 Markdown 正文直接输出，不要只给提纲，不要说「见附件」
"""

_SLOT_PROMPTS: dict[str, str] = {
    "astock_market": """你是 A 股**收盘播报编辑**（不是板块研究员、不是个股故事写手）。
请联网核对 A 股「最近一个**已完整收盘**的交易日」的收盘数据，写一篇 **每日报盘 + 简单分析**（全文 1000–1600 字，宁可短也不要跑题）。

【交易日判定（重要）】
- 写的是「最近一个已经收盘的交易日」，不是日历上的今天。例如：周六/周日或交易日盘中、收盘前运行，都要取**上一个已收盘交易日**（周一早上跑 → 取上周五）。
- 在正文**第一行**单独输出一行机器可读标记：`交易日：YYYY-MM-DD`（你联网确认的那个已收盘交易日）。这行必须存在、放最前面。

# 一级标题
紧接着写一级标题，格式固定为「{{月}}月{{日}}日A股大盘分析」，其中「月/日」取上面那行的交易日（不是今天）。例如交易日是 2026-06-04，标题就是「6月4日A股大盘分析」。禁止写成问句或别的说法。

## 一、今日报盘（只摆数据，不超过 350 字）
必须按条目写出（有则写数字，没有则写「数据待核实」）：
- 上证指数：收盘点位、涨跌幅
- 深证成指：收盘点位、涨跌幅
- 创业板指：收盘点位、涨跌幅
- 科创50：收盘点位、涨跌幅
- 沪深两市成交额、较前一交易日缩量或放量多少
- 上涨家数 / 下跌家数 / 平盘（注明媒体口径若不一致）

## 二、盘面一句话（不超过 60 字）
用一句话概括今日市场性格（如：指数小跌、个股普跌、缩量观望、结构分化等）。

## 三、行业涨跌一览（不超过 200 字）
- 领涨行业 3 个：只写行业名 + 大致涨跌幅，每个行业最多补 1 句事实
- 领跌行业 3 个：同上
禁止：把某一概念（MLCC、存储、CPO、某只个股）写成半篇文章。

## 四、简单分析（不超过 350 字，共 3 点，每点 2–3 句）
只回答：
1）指数为什么这样走（结合量能/结构，不要预测明天涨跌）
2）涨跌家数说明什么情绪
3）今天是普涨、普跌还是二八分化（举 1 个行业例子即可）

## 五、明日关注（3 条 bullet，每条 1 句，客观线索，非操作建议）

【严禁跑题 — 违反则视为失败】
- 禁止问句/悬念标题（如「钱去哪了」「为什么跌」）
- 禁止单一板块/概念深度分析（MLCC、半导体涨价、存储周期等最多提 1 次、不超过 15 字）
- 禁止个股案例、龙虎榜、妖股、连板
- 禁止国际宏观（美联储、美股）占篇幅
- 禁止「加仓」「减仓」「割肉」「抄底」等操作建议措辞；资金描述用「净流入/净流出」
""" + _COMMON_RULES,
    "astock_sector": """你是 A 股板块研究员。请联网搜索 A 股「最新一个交易日」盘面。

先判断 **唯一** 最值得写的最热板块/概念（如 MLCC、存储芯片、工业气体等），再写一篇 **该方向的专题分析**（1500–2500 字），结构：
1. 约 200 字说明为何选它（大盘背景 + 为何不做全盘收评）
2. 一句话结论
3. 板块是什么、产业链位置（小白能懂）
4. 今天为什么涨（盘面事实 + 消息催化，要有数字）
5. 逻辑能否持续（供需/涨价/政策/海外映射）
6. 风险提示 3 条
7. 结语

只写一个板块，不要罗列多个题材。
""" + _COMMON_RULES,
    "domestic": """你是国内财经评论员。请联网搜索中国大陆 **最近 48 小时** 最受关注的财经新闻（宏观政策、监管、产业、消费、地产、金融等，不限于股市）。

写一篇 **国内财经新闻分析**（1500–2500 字），结构：
1. 标题含核心事件
2. 一句话结论
3. 事件还原（谁、何时、发生了什么）
4. 为什么重要（对产业/普通人/资金面的影响）
5. 各方可能反应（政府、企业、市场，客观陈述）
6. 后续观察点 3 条
7. 结语

优先选 **一条** 主线新闻深入写；不要堆砌多条快讯。
""" + _COMMON_RULES,
    "ai": """你是 AI 产业观察员。请联网搜索 **全球最近 48 小时** AI 领域最热新闻（大模型、芯片、监管、巨头战略、融资、开源、Agent 等）。

写一篇 **AI 新闻热点分析**（1500–2500 字），结构：
1. 标题含事件/产品名
2. 一句话结论
3. 事件还原（用大白话）
4. 技术或商业上「新在哪」
5. 对产业链/竞争格局的影响
6. 对普通人意味着什么（客观，不夸张）
7. 风险提示或争议点 2–3 条
8. 结语

只选 **一个** 最热话题写透。
""" + _COMMON_RULES,
    "world": """你是国际财经编辑。请联网搜索 **全球最近 48 小时** 世界财经主线（美联储/欧央行、美股、美债、美元、油价、黄金、地缘、主要经济体数据等）。

写一篇 **世界财经新闻分析**（1500–2500 字），结构：
1. 标题含事件
2. 一句话结论
3. 事件还原
4. 传导链条（如何影响汇率、风险偏好、新兴市场等）
5. 与中国的关联（若有，客观简述）
6. 后续观察点 3 条
7. 结语

只选 **一条** 国际主线写透，不要写成日报列表。
""" + _COMMON_RULES,
}


def planned_slots(
    target: int,
    *,
    start_offset: int = 0,
    skip_astock_market: bool = False,
    skip_slots: frozenset[str] | None = None,
) -> list[str]:
    if target <= 0:
        return []
    n = len(CURSOR_SLOT_ORDER)
    slots_to_skip = skip_slots
    if slots_to_skip is None:
        slots_to_skip = OFFDAY_SKIP_SLOTS if skip_astock_market else frozenset()
    if not slots_to_skip:
        return [CURSOR_SLOT_ORDER[(start_offset + i) % n] for i in range(target)]
    out: list[str] = []
    i = 0
    while len(out) < target and i < n * max(target, 1):
        slot = CURSOR_SLOT_ORDER[(start_offset + i) % n]
        i += 1
        if slot in slots_to_skip:
            continue
        out.append(slot)
    return out


def _today_queue_offset() -> int:
    """接在今日已完成的 cursor 槽位之后续排。"""
    try:
        from batch_aivideo import recent_history
    except Exception:  # noqa: BLE001
        return 0
    local_offset = timedelta(hours=8)
    today = (datetime.now(timezone.utc) + local_offset).date()
    latest: dict | None = None
    latest_ts: datetime | None = None
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
        if (ts.astimezone(timezone.utc) + local_offset).date() != today:
            continue
        if latest_ts is None or ts > latest_ts:
            latest_ts = ts
            latest = item
    if not latest:
        return 0
    slot = str(
        latest.get("topic_slot")
        or latest.get("direction")
        or latest.get("cursor_slot")
        or ""
    ).strip().lower()
    if slot not in CURSOR_SLOT_ORDER:
        return 0
    return (CURSOR_SLOT_ORDER.index(slot) + 1) % len(CURSOR_SLOT_ORDER)


def discover_cursor_topics(*, target: int = 5) -> list[dict]:
    """生成今日固定槽位话题列表（不调 Exa、不调 Opus 选题）。"""
    load_env()
    start = _today_queue_offset()
    skip_market = should_skip_astock_market_today()
    if skip_market:
        d = china_today()
        weekday = "六日"[d.weekday() - 5] if d.weekday() >= 5 else ""
        reason = f"周{weekday}" if weekday else f"{d.isoformat()}（法定节假日）"
        skipped = "」「".join(SLOT_LABEL[s] for s in OFFDAY_SKIP_SLOTS)
        print(
            f"  ⏭  今日非工作日（{reason}），跳过槽位「{skipped}」",
            flush=True,
        )
    slots = planned_slots(target, start_offset=start, skip_astock_market=skip_market)
    today = china_today().isoformat()
    topics: list[dict] = []
    for i, slot in enumerate(slots, 1):
        label = SLOT_LABEL[slot]
        plan = topic_plan_for_slot(slot)
        row = {
            "index": i,
            "slot": slot,
            "direction": slot,
            "cursor_slot": slot,
            "title_hint": plan.get("title_hint") or f"{today} {label}",
            "category": SLOT_TO_CATEGORY.get(slot, "ai"),
            "theme_cluster": plan.get("theme_cluster") or f"cursor_{slot}",
            "angle": plan.get("angle") or label,
            "reason": f"固定槽位 #{i}：{label}",
        }
        if plan.get("fixed_video_title"):
            row["fixed_video_title"] = plan["fixed_video_title"]
        if plan.get("cold_open"):
            row["cold_open"] = plan["cold_open"]
        if plan.get("script_mode"):
            row["script_mode"] = plan["script_mode"]
        topics.append(row)
    return topics


def _extract_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return re.sub(r"^#+\s*", "", line).strip()[:80] or fallback
    return fallback[:80]


def _save_draft(slot: str, markdown: str, meta: dict) -> Path:
    drafts = ROOT / "logs" / "cursor_drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = drafts / f"{stamp}_{slot}.md"
    header = (
        f"<!-- slot={slot} model={model_id()} "
        f"agent={meta.get('agent_id')} run={meta.get('run_id')} -->\n\n"
    )
    path.write_text(header + markdown.strip() + "\n", encoding="utf-8")
    meta_path = path.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_cursor_draft(
    slot: str,
    *,
    agent_id: str | None = None,
    on_assistant=None,
) -> tuple[str, str, str]:
    """调用 Cloud Agent 生成该槽位 Markdown 草稿。返回 (markdown, agent_id, status)。"""
    if slot not in _SLOT_PROMPTS:
        raise ValueError(f"未知槽位: {slot}")
    today = date.today().isoformat()
    raw = _SLOT_PROMPTS[slot]
    prompt = (
        f"【当前日期参考】{today}（注意：A股大盘报盘要写最近一个已收盘交易日，未必是今天）\n"
        f"【本任务类型】{SLOT_LABEL[slot]}\n\n"
        + raw
    )
    print(f"  ☁️  Cursor Cloud Agent · {SLOT_LABEL[slot]} · model={model_id()}", flush=True)
    if agent_id:
        run_id = create_run(agent_id, prompt)
        print(f"     复用 agent={agent_id} 新 run={run_id}", flush=True)
    else:
        agent_id, run_id = create_agent(prompt)
        print(f"     新建 agent={agent_id} run={run_id}", flush=True)

    chunks: list[str] = []

    def _on_delta(t: str) -> None:
        chunks.append(t)
        if on_assistant:
            on_assistant(t)
        else:
            sys.stdout.write(t)
            sys.stdout.flush()

    text, status = run_with_stream(agent_id, run_id, on_assistant=_on_delta)
    body = (text or "".join(chunks)).strip()
    if not body:
        raise RuntimeError(f"Cursor Agent 未返回正文（slot={slot} status={status}）")
    return body, agent_id, status


def build_cursor_topic_research(
    topic: dict,
    *,
    agent_id: str | None = None,
    on_assistant=None,
) -> tuple[dict, dict, str | None]:
    """Cursor 草稿 → Opus 深读细节。返回 (article, details, agent_id)。"""
    slot = str(topic.get("slot") or topic.get("cursor_slot") or topic.get("direction") or "").strip()
    if slot not in CURSOR_SLOT_ORDER:
        raise ValueError(f"话题缺少有效 cursor 槽位: {topic}")

    markdown, agent_id, status = run_cursor_draft(
        slot, agent_id=agent_id, on_assistant=on_assistant,
    )
    if status != "FINISHED":
        print(f"  ⚠️  Agent 状态={status}，仍尝试用已返回正文继续", file=sys.stderr)

    meta = {
        "slot": slot,
        "status": status,
        "agent_id": agent_id,
        "model": model_id(),
        "title_hint": topic.get("title_hint"),
    }
    draft_path = _save_draft(slot, markdown, meta)
    print(f"  ✓ Cursor 草稿已保存: {draft_path} ({len(markdown)} 字)")

    plan = topic_plan_for_slot(slot)
    for key in (
        "fixed_video_title", "cold_open", "script_mode",
        "title_hint", "angle", "theme_cluster",
    ):
        if topic.get(key):
            plan[key] = topic[key]
    fixed_title = str(
        topic.get("fixed_video_title") or plan.get("fixed_video_title") or ""
    ).strip()

    # 大盘报盘：以正文里的「实际交易日」为准生成标题/冷开场，避免写死成今天。
    if slot == "astock_market":
        trading_day = _extract_trading_date(markdown)
        if trading_day:
            fixed_title = market_title_for_date(trading_day)
            plan["fixed_video_title"] = fixed_title
            plan["cold_open"] = astock_market_cold_open(trading_day)
            print(f"  📅 报盘交易日：{trading_day.isoformat()} → 标题「{fixed_title}」")
        else:
            print("  ⚠️  未能从草稿解析交易日，沿用今日日期标题", file=sys.stderr)

    fallback = fixed_title or str(topic.get("title_hint") or SLOT_LABEL[slot])
    title = fixed_title or _extract_title(markdown, fallback)
    if slot == "astock_market" and fixed_title:
        title = fixed_title

    article = {
        "title": title,
        "question_title": "",
        "url": f"cursor-draft://{draft_path.name}",
        "site": "cursor-cloud-agent",
        "author": model_id(),
        "published_at": date.today().isoformat(),
        "language": "zh",
        "summary_zh": markdown[:500],
        "thesis": title,
        "key_facts": [],
        "narrative_arc": "A股每日收盘报盘",
        "source_type": f"cursor:{slot}",
        "_cursor_draft": str(draft_path),
        "_compliance_relaxed": True,
        "_topic_plan": plan,
    }
    if fixed_title:
        article["_fixed_video_title"] = fixed_title

    print(f"  🤖 Opus 深读 Cursor 草稿（抽取短视频素材）…")
    details, _ = deep_read_article(article, agent_id=None, full_text=markdown)
    return article, details, agent_id
