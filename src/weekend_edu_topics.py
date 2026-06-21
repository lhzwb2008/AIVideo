#!/usr/bin/env python3
"""周末科普教育选题：与工作日新闻槽位分离，默认 3 条/次，带历史去重。

典型话题：「量化交易是什么」「市盈率怎么算」「企业 EV 价值如何算」。
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from cursor_client import create_agent, create_run, model_id, run_with_stream
from cursor_daily_topics import CURSOR_SLOT_ORDER, china_today
from paths import ROOT
from research import deep_read_article, load_env

# 每次周末跑 3 条，各从一个栏目池里选题
EDU_CATEGORY_ORDER = ("basic", "quant", "valuation")

EDU_SLOT_LABEL: dict[str, str] = {
    "basic": "财经基础科普",
    "quant": "量化入门科普",
    "valuation": "估值与计算科普",
}

# topic_id, title, category, theme_cluster
EDU_TOPIC_CATALOG: tuple[dict, ...] = (
    # —— 基础概念 ——
    {"topic_id": "pe_ratio", "title": "市盈率是什么，怎么算", "category": "basic", "theme_cluster": "edu_pe_ratio"},
    {"topic_id": "pb_ratio", "title": "市净率是什么，怎么看", "category": "basic", "theme_cluster": "edu_pb_ratio"},
    {"topic_id": "ps_ratio", "title": "市销率是什么，什么时候用", "category": "basic", "theme_cluster": "edu_ps_ratio"},
    {"topic_id": "roe", "title": "ROE 是什么，为什么投资人常看", "category": "basic", "theme_cluster": "edu_roe"},
    {"topic_id": "gross_net_margin", "title": "毛利率和净利率有什么区别", "category": "basic", "theme_cluster": "edu_margin"},
    {"topic_id": "cash_flow_types", "title": "经营现金流、自由现金流是什么", "category": "basic", "theme_cluster": "edu_cashflow"},
    {"topic_id": "three_statements", "title": "财报三张表分别看什么", "category": "basic", "theme_cluster": "edu_financial_statements"},
    {"topic_id": "debt_ratio", "title": "资产负债率怎么看才不算高", "category": "basic", "theme_cluster": "edu_debt_ratio"},
    {"topic_id": "dividend_yield", "title": "分红和股息率是什么", "category": "basic", "theme_cluster": "edu_dividend"},
    {"topic_id": "compound_interest", "title": "复利是什么，为什么时间很重要", "category": "basic", "theme_cluster": "edu_compound"},
    {"topic_id": "inflation_deflation", "title": "通胀和通缩分别是什么意思", "category": "basic", "theme_cluster": "edu_inflation"},
    {"topic_id": "bull_bear", "title": "牛市和熊市怎么定义", "category": "basic", "theme_cluster": "edu_bull_bear"},
    {"topic_id": "long_short", "title": "做多和做空到底是什么意思", "category": "basic", "theme_cluster": "edu_long_short"},
    {"topic_id": "leverage", "title": "杠杆是什么，为什么能放大盈亏", "category": "basic", "theme_cluster": "edu_leverage"},
    {"topic_id": "stop_loss", "title": "止损是什么，不是让你马上卖", "category": "basic", "theme_cluster": "edu_stop_loss"},
    {"topic_id": "volume_price", "title": "成交量和价格是什么关系", "category": "basic", "theme_cluster": "edu_volume"},
    {"topic_id": "kline_ma", "title": "K 线和均线入门怎么读", "category": "basic", "theme_cluster": "edu_kline"},
    {"topic_id": "goodwill", "title": "商誉是什么，为什么并购会出现", "category": "basic", "theme_cluster": "edu_goodwill"},
    {"topic_id": "ipo_prospectus", "title": "招股书里最该先看哪几段", "category": "basic", "theme_cluster": "edu_ipo"},
    {"topic_id": "rate_hike_cut", "title": "加息和降息分别影响什么", "category": "basic", "theme_cluster": "edu_rates"},
    {"topic_id": "gdp_cpi_ppi", "title": "GDP、CPI、PPI 分别说明什么", "category": "basic", "theme_cluster": "edu_macro_indicators"},
    {"topic_id": "bond_yield", "title": "国债收益率上升下降意味着什么", "category": "basic", "theme_cluster": "edu_bond_yield"},
    # —— 量化入门 ——
    {"topic_id": "quant_trading", "title": "量化交易是什么", "category": "quant", "theme_cluster": "edu_quant_trading"},
    {"topic_id": "factor_investing", "title": "因子投资是什么", "category": "quant", "theme_cluster": "edu_factor"},
    {"topic_id": "multi_factor", "title": "多因子模型入门怎么理解", "category": "quant", "theme_cluster": "edu_multi_factor"},
    {"topic_id": "backtest", "title": "回测是什么，为什么不能全信", "category": "quant", "theme_cluster": "edu_backtest"},
    {"topic_id": "alpha_beta", "title": "阿尔法和贝塔分别是什么", "category": "quant", "theme_cluster": "edu_alpha_beta"},
    {"topic_id": "sharpe_ratio", "title": "夏普比率是什么，怎么理解", "category": "quant", "theme_cluster": "edu_sharpe"},
    {"topic_id": "max_drawdown", "title": "最大回撤是什么", "category": "quant", "theme_cluster": "edu_drawdown"},
    {"topic_id": "algo_trading", "title": "程序化交易和手动交易差在哪", "category": "quant", "theme_cluster": "edu_algo"},
    {"topic_id": "hft", "title": "高频交易是什么，和普通量化有何不同", "category": "quant", "theme_cluster": "edu_hft"},
    {"topic_id": "mean_reversion", "title": "均值回归策略是什么思路", "category": "quant", "theme_cluster": "edu_mean_reversion"},
    {"topic_id": "momentum", "title": "动量策略是什么", "category": "quant", "theme_cluster": "edu_momentum"},
    {"topic_id": "smart_beta", "title": "Smart Beta 是什么", "category": "quant", "theme_cluster": "edu_smart_beta"},
    # —— 估值与计算 ——
    {"topic_id": "enterprise_value", "title": "企业 EV 价值怎么算", "category": "valuation", "theme_cluster": "edu_ev"},
    {"topic_id": "dcf_basics", "title": "现金流折现 DCF 是什么思路", "category": "valuation", "theme_cluster": "edu_dcf"},
    {"topic_id": "ev_ebitda", "title": "EV/EBITDA 是什么，何时比 PE 更好", "category": "valuation", "theme_cluster": "edu_ev_ebitda"},
    {"topic_id": "peg_ratio", "title": "PEG 是什么，怎么结合成长看估值", "category": "valuation", "theme_cluster": "edu_peg"},
    {"topic_id": "relative_valuation", "title": "相对估值和绝对估值有什么区别", "category": "valuation", "theme_cluster": "edu_relative_val"},
    {"topic_id": "nav_discount", "title": "净资产和市值为什么经常不一样", "category": "valuation", "theme_cluster": "edu_nav"},
    {"topic_id": "wacc", "title": "WACC 加权平均资本成本是什么", "category": "valuation", "theme_cluster": "edu_wacc"},
    {"topic_id": "terminal_value", "title": "DCF 里终值为什么很重要", "category": "valuation", "theme_cluster": "edu_terminal_value"},
    {"topic_id": "sotp", "title": "分部估值 SOTP 是什么", "category": "valuation", "theme_cluster": "edu_sotp"},
    {"topic_id": "ev_sales", "title": "EV/Sales 适合什么类型的公司", "category": "valuation", "theme_cluster": "edu_ev_sales"},
)

_EDU_DRAFT_PROMPT = """你是「AI财知道」的**财经科普教育撰稿人**（不是新闻编辑、不是收评写手）。

请围绕下面这个**固定科普话题**写一篇面向零基础观众的中文 Markdown 长文（1200–2000 字）：

【科普话题】{title}

写作要求：
1. **只讲概念、原理、公式怎么算、怎么理解**——不要写成当日新闻、不要写具体个股涨跌、不要写「今天市场」
2. 结构建议：
   - 一级标题：用问句或「X是什么/怎么算」形式，与话题一致
   - 一句话结论（60 字内）
   - 先讲「它解决什么问题」（生活化类比 1–2 个）
   - 定义与核心公式/计算步骤（分步写清，举例用虚构的简化数字）
   - 常见误读 2–3 条
   - 和相邻概念的区别（若有）
   - 小结：普通人怎么用这个概念理解财经新闻（不给买卖建议）
3. 可联网查证标准定义、经典公式与教材表述，交叉验证；**禁止编造**搜不到的具体公司股价或日期
4. **严禁**：股票代码、荐股、目标价、买卖建议、仓位建议、保证收益
5. 全文中文 Markdown，直接输出正文，不要提纲-only，不要说「见附件」
"""


def is_weekend_edu_mode(d: date | None = None) -> bool:
    """是否走周末科普模式（周六日；可用 AIVIDEO_FORCE_WEEKDAY=1 强制工作日槽位）。"""
    if os.environ.get("AIVIDEO_FORCE_WEEKDAY", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    if os.environ.get("AIVIDEO_FORCE_WEEKEND_EDU", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    d = d or china_today()
    return d.weekday() >= 5


def weekend_default_count() -> int:
    raw = os.environ.get("AIVIDEO_WEEKEND_MAX_VIDEOS", "3").strip()
    try:
        return max(1, min(int(raw), len(EDU_CATEGORY_ORDER)))
    except ValueError:
        return 3


def edu_dedup_days() -> int:
    raw = os.environ.get("AIVIDEO_EDU_DEDUP_DAYS", "90").strip()
    try:
        return max(7, int(raw))
    except ValueError:
        return 90


def _norm_title(text: str) -> str:
    t = re.sub(r"\s+", "", (text or "").lower())
    return re.sub(r"[？?！!。，、：:；;「」\"'（）()【】\[\]·\-—]", "", t)


def _titles_overlap(a: str, b: str) -> bool:
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 6 and shorter in longer:
        return True
    return False


def _recent_edu_usage() -> tuple[set[str], set[str], dict[str, datetime]]:
    """返回 (已用 topic_id, 已用归一化标题, topic_id -> 最近使用时间)。"""
    from batch_aivideo import recent_history

    used_ids: set[str] = set()
    used_titles: set[str] = set()
    last_used: dict[str, datetime] = {}
    for item in recent_history(edu_dedup_days()):
        tid = str(item.get("topic_id") or "").strip()
        slot = str(item.get("topic_slot") or item.get("direction") or "").strip()
        mode = str(item.get("mode") or "").strip()
        is_edu = mode == "weekend_edu" or slot.startswith("edu_")
        if not is_edu and not tid:
            title = str(item.get("script_title") or item.get("title") or "").strip()
            if title:
                for row in EDU_TOPIC_CATALOG:
                    if _titles_overlap(title, row["title"]):
                        tid = row["topic_id"]
                        is_edu = True
                        break
        if not is_edu:
            continue
        if not tid:
            title = str(item.get("script_title") or item.get("title") or "").strip()
            for row in EDU_TOPIC_CATALOG:
                if _titles_overlap(title, row["title"]):
                    tid = row["topic_id"]
                    break
        if tid:
            used_ids.add(tid)
        for key in ("script_title", "title", "title_hint"):
            t = str(item.get(key) or "").strip()
            if t:
                used_titles.add(_norm_title(t))
        made_at = str(item.get("made_at") or "").strip()
        if tid and made_at:
            try:
                ts = datetime.fromisoformat(made_at.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if tid not in last_used or ts > last_used[tid]:
                    last_used[tid] = ts
            except ValueError:
                pass
    return used_ids, used_titles, last_used


def _topic_duplicate_reason(
    row: dict,
    *,
    used_ids: set[str],
    used_titles: set[str],
    batch_counts: dict[str, int],
) -> str:
    tid = row["topic_id"]
    if tid in used_ids:
        return f"话题 id「{tid}」近 {edu_dedup_days()} 天已制作"
    title = row["title"]
    for ut in used_titles:
        if _titles_overlap(title, ut):
            return f"标题与近期已做话题过于相似"
    try:
        from theme_clusters import cluster_duplicate_reason

        reason = cluster_duplicate_reason(
            {
                "theme_cluster": row["theme_cluster"],
                "title_hint": title,
            },
            extra_counts=batch_counts,
        )
        if reason:
            return reason
    except Exception:  # noqa: BLE001
        pass
    return ""


def _pick_for_category(
    category: str,
    *,
    used_ids: set[str],
    used_titles: set[str],
    batch_counts: dict[str, int],
    last_used: dict[str, datetime],
) -> dict | None:
    candidates = [r for r in EDU_TOPIC_CATALOG if r["category"] == category]
    fresh: list[dict] = []
    for row in candidates:
        if _topic_duplicate_reason(
            row,
            used_ids=used_ids,
            used_titles=used_titles,
            batch_counts=batch_counts,
        ):
            continue
        fresh.append(row)
    if not fresh:
        return None
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    fresh.sort(key=lambda r: last_used.get(r["topic_id"], epoch))
    return fresh[0]


def _pick_fallback(
    *,
    used_ids: set[str],
    used_titles: set[str],
    batch_counts: dict[str, int],
    last_used: dict[str, datetime],
) -> dict | None:
    fresh: list[dict] = []
    for row in EDU_TOPIC_CATALOG:
        if _topic_duplicate_reason(
            row,
            used_ids=used_ids,
            used_titles=used_titles,
            batch_counts=batch_counts,
        ):
            continue
        fresh.append(row)
    if not fresh:
        return None
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    fresh.sort(key=lambda r: last_used.get(r["topic_id"], epoch))
    return fresh[0]


def topic_plan_for_edu(row: dict) -> dict:
    cat = row["category"]
    return {
        "slot": f"edu_{cat}",
        "script_mode": "edu_explain",
        "title_hint": row["title"],
        "suggested_video_title": row["title"],
        "cold_open": f"今天搞懂一个财经概念：{row['title'].split('，')[0].split('怎么')[0]}",
        "angle": EDU_SLOT_LABEL.get(cat, "财经科普"),
        "theme_cluster": row["theme_cluster"],
        "topic_id": row["topic_id"],
        "category": "quant" if cat == "quant" else "basic",
        "direction": f"edu_{cat}",
    }


def discover_weekend_edu_topics(*, target: int | None = None) -> list[dict]:
    """周末科普选题：默认 3 条，分基础/量化/估值，避开近期已做话题。"""
    load_env()
    target = target if target is not None else weekend_default_count()
    target = max(1, min(target, len(EDU_CATEGORY_ORDER)))

    used_ids, used_titles, last_used = _recent_edu_usage()
    if used_ids or used_titles:
        print(
            f"  📚 周末科普去重：近 {edu_dedup_days()} 天已做 {len(used_ids)} 个话题",
            flush=True,
        )

    categories = list(EDU_CATEGORY_ORDER[:target])
    topics: list[dict] = []
    batch_counts: dict[str, int] = {}

    for i, cat in enumerate(categories, 1):
        row = _pick_for_category(
            cat,
            used_ids=used_ids,
            used_titles=used_titles,
            batch_counts=batch_counts,
            last_used=last_used,
        )
        if not row:
            row = _pick_fallback(
                used_ids=used_ids,
                used_titles=used_titles,
                batch_counts=batch_counts,
                last_used=last_used,
            )
        if not row:
            print(f"  ⚠️  栏目「{EDU_SLOT_LABEL[cat]}」无可用新话题（池子可能已用尽）", flush=True)
            continue

        plan = topic_plan_for_edu(row)
        topics.append({
            "index": i,
            "slot": plan["slot"],
            "direction": plan["direction"],
            "cursor_slot": plan["slot"],
            "title_hint": plan["title_hint"],
            "category": plan["category"],
            "theme_cluster": plan["theme_cluster"],
            "topic_id": row["topic_id"],
            "angle": plan["angle"],
            "cold_open": plan["cold_open"],
            "script_mode": plan["script_mode"],
            "suggested_video_title": plan["suggested_video_title"],
            "reason": f"周末科普 · {EDU_SLOT_LABEL[cat]} · {row['title']}",
            "mode": "weekend_edu",
        })
        used_ids.add(row["topic_id"])
        used_titles.add(_norm_title(row["title"]))
        cid = row["theme_cluster"]
        batch_counts[cid] = batch_counts.get(cid, 0) + 1

    return topics


def topic_for_edu_slot(slot: str) -> dict:
    """--slot edu_basic / edu_quant / edu_valuation 重跑单条。"""
    cat = slot.replace("edu_", "", 1) if slot.startswith("edu_") else slot
    if cat not in EDU_CATEGORY_ORDER:
        raise ValueError(f"未知周末科普槽位: {slot}")
    used_ids, used_titles, last_used = _recent_edu_usage()
    row = _pick_for_category(
        cat,
        used_ids=used_ids,
        used_titles=used_titles,
        batch_counts={},
        last_used=last_used,
    )
    if not row:
        row = next(r for r in EDU_TOPIC_CATALOG if r["category"] == cat)
    plan = topic_plan_for_edu(row)
    return {
        "index": 1,
        "slot": plan["slot"],
        "direction": plan["direction"],
        "cursor_slot": plan["slot"],
        "title_hint": plan["title_hint"],
        "category": plan["category"],
        "theme_cluster": plan["theme_cluster"],
        "topic_id": row["topic_id"],
        "angle": plan["angle"],
        "cold_open": plan["cold_open"],
        "script_mode": plan["script_mode"],
        "suggested_video_title": plan["suggested_video_title"],
        "reason": f"指定周末科普槽位：{EDU_SLOT_LABEL[cat]}",
        "mode": "weekend_edu",
    }


def _save_draft(slot: str, markdown: str, meta: dict) -> Path:
    drafts = ROOT / "logs" / "edu_drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = drafts / f"{stamp}_{slot}.md"
    header = (
        f"<!-- slot={slot} mode=weekend_edu model={model_id()} "
        f"agent={meta.get('agent_id')} run={meta.get('run_id')} -->\n\n"
    )
    path.write_text(header + markdown.strip() + "\n", encoding="utf-8")
    meta_path = path.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_edu_draft(
    topic: dict,
    *,
    agent_id: str | None = None,
    on_assistant=None,
) -> tuple[str, str, str]:
    """Cursor 写科普长文草稿。"""
    title = str(topic.get("title_hint") or topic.get("title") or "").strip()
    slot = str(topic.get("slot") or "edu_basic")
    prompt = (
        f"【当前日期参考】{china_today().isoformat()}\n"
        f"【任务类型】周末财经科普教育（非新闻）\n\n"
        + _EDU_DRAFT_PROMPT.format(title=title)
    )
    print(f"  ☁️  Cursor 科普写稿 · {title} · model={model_id()}", flush=True)
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
        raise RuntimeError(f"Cursor Agent 未返回科普正文（slot={slot} status={status}）")
    return body, agent_id, status


def _extract_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return re.sub(r"^#+\s*", "", line).strip()[:80] or fallback
    return fallback[:80]


def build_weekend_edu_research(
    topic: dict,
    *,
    agent_id: str | None = None,
    on_assistant=None,
) -> tuple[dict, dict, str | None]:
    """科普 Cursor 草稿 → Opus 深读 → 返回 (article, details, agent_id)。"""
    slot = str(topic.get("slot") or "edu_basic")
    title_hint = str(topic.get("title_hint") or "").strip()
    markdown, agent_id, status = run_edu_draft(
        topic, agent_id=agent_id, on_assistant=on_assistant,
    )
    if status != "FINISHED":
        print(f"  ⚠️  Agent 状态={status}，仍尝试用已返回正文继续", file=sys.stderr)

    meta = {
        "slot": slot,
        "mode": "weekend_edu",
        "topic_id": topic.get("topic_id"),
        "status": status,
        "agent_id": agent_id,
        "model": model_id(),
        "title_hint": title_hint,
    }
    draft_path = _save_draft(slot, markdown, meta)
    print(f"  ✓ 科普草稿已保存: {draft_path} ({len(markdown)} 字)")

    plan = topic_plan_for_edu({
        "topic_id": topic.get("topic_id") or "",
        "title": title_hint,
        "category": (slot.replace("edu_", "", 1) if slot.startswith("edu_") else "basic"),
        "theme_cluster": str(topic.get("theme_cluster") or ""),
    })
    for key in (
        "suggested_video_title", "cold_open", "script_mode",
        "title_hint", "angle", "theme_cluster", "topic_id", "category",
    ):
        if topic.get(key):
            plan[key] = topic[key]

    video_title = str(topic.get("suggested_video_title") or title_hint).strip()
    title = video_title or _extract_title(markdown, title_hint)

    article = {
        "title": title,
        "question_title": title_hint,
        "url": f"cursor-edu-draft://{draft_path.name}",
        "site": "cursor-cloud-agent",
        "author": model_id(),
        "published_at": china_today().isoformat(),
        "language": "zh",
        "summary_zh": markdown[:500],
        "thesis": title_hint,
        "key_facts": [],
        "narrative_arc": "财经科普讲解",
        "source_type": f"cursor:edu:{slot}",
        "_cursor_draft": str(draft_path),
        "_compliance_relaxed": True,
        "_topic_plan": plan,
    }
    if video_title:
        article["_suggested_video_title"] = video_title

    print("  🤖 Opus 深读科普草稿（抽取短视频素材）…")
    details, _ = deep_read_article(article, agent_id=None, full_text=markdown)
    return article, details, agent_id


ALL_SLOT_CHOICES = tuple(list(CURSOR_SLOT_ORDER) + [f"edu_{c}" for c in EDU_CATEGORY_ORDER])
