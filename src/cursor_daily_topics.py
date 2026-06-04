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


def fixed_market_video_title(d: date | None = None) -> str:
    """第一槽位固定视频/文章标题，如「6月4日A股大盘分析」。"""
    d = d or date.today()
    return f"{d.month}月{d.day}日A股大盘分析"


_COMMON_RULES = """
硬性要求（全部槽位通用）：
- 必须联网检索「最新交易日/最近 48 小时」公开报道（财联社、证券时报、新浪财经、东方财富、金融界、华尔街见闻、Reuters、Bloomberg 等），交叉验证
- 禁止编造搜不到的股价、涨跌幅、成交额；不确定写「数据待核实」
- 禁止股票代码、荐股、目标价、买卖建议、仓位建议
- 全文中文 Markdown；文末一行：*免责声明：本文为信息梳理，不构成投资建议。*
- 把完整 Markdown 正文直接输出，不要只给提纲，不要说「见附件」
"""

_SLOT_PROMPTS: dict[str, str] = {
    "astock_market": """你是 A 股日终复盘作者。请联网搜索 A 股「最新一个交易日」的大盘与资金面。

写一篇 **A股大盘报盘与分析**（1800–2800 字），结构：
1. 文章一级标题（# 标题）必须 exactly 为：{market_title}（禁止改成「报盘与分析」「大盘复盘」等其它说法）
2. 一句话结论（今日市场性格：进攻/防守/分化等）
3. 三大指数 + 科创50/北证50 收盘表现（点位、涨跌幅）
4. 量能：成交额、较昨日缩量/放量、涨跌家数
5. 板块与风格：领涨/领跌行业、资金主线（各举 2–3 个方向，不展开成个股推荐）
6. 北向/主力/情绪指标（有数据则写，无则标注待核实）
7. 与前一交易日对比：情绪是改善还是恶化
8. 后市观察（3 条客观线索，非操作建议）
9. 结语

不要写成单一概念板块深度稿；本篇重点是 **全景报盘**。
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


def planned_slots(target: int, *, start_offset: int = 0) -> list[str]:
    if target <= 0:
        return []
    n = len(CURSOR_SLOT_ORDER)
    return [CURSOR_SLOT_ORDER[(start_offset + i) % n] for i in range(target)]


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
    slots = planned_slots(target, start_offset=start)
    today = date.today().isoformat()
    topics: list[dict] = []
    for i, slot in enumerate(slots, 1):
        label = SLOT_LABEL[slot]
        topics.append({
            "index": i,
            "slot": slot,
            "direction": slot,
            "cursor_slot": slot,
            "title_hint": f"{today} {label}",
            "category": SLOT_TO_CATEGORY.get(slot, "ai"),
            "theme_cluster": f"cursor_{slot}",
            "angle": label,
            "reason": f"固定槽位 #{i}：{label}",
        })
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
    prompt = (
        f"【当前日期参考】{today}\n"
        f"【本任务类型】{SLOT_LABEL[slot]}\n\n"
        + _SLOT_PROMPTS[slot]
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

    title = _extract_title(markdown, str(topic.get("title_hint") or SLOT_LABEL[slot]))
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
        "narrative_arc": SLOT_LABEL[slot],
        "source_type": f"cursor:{slot}",
        "_cursor_draft": str(draft_path),
        "_compliance_relaxed": True,
    }

    print(f"  🤖 Opus 深读 Cursor 草稿（抽取短视频素材）…")
    details, _ = deep_read_article(article, agent_id=None, full_text=markdown)
    return article, details, agent_id
