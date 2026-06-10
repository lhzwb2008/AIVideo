#!/usr/bin/env python3
"""US Market Cursor 写稿：联网调研英文长文 → Opus 深读 → 英文短视频脚本。

不走 Exa 搜文；模仿 cursor_daily_topics / make_publish_new。
每次 1 条，槽位轮换：大盘速览 → 巨头热点 → 宏观。
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from cursor_client import create_agent, create_run, model_id, run_with_stream
from paths import ROOT
from research import load_env
from us_market import adapt_us_script, deep_read_us_markdown

US_SLOT_ORDER = (
    "us_wrap",
    "mega_cap",
    "macro",
)

SLOT_LABEL: dict[str, str] = {
    "us_wrap": "US market wrap",
    "mega_cap": "Mega-cap hot spot",
    "macro": "Macro & Fed",
    "sector": "Sector rotation",
}

_COMMON_RULES = """
Hard rules (all slots):
- Search the web for facts from the last 24-72 hours (Reuters, Bloomberg, WSJ, CNBC, MarketWatch, Yahoo Finance, Fed releases, etc.). Cross-check.
- Do NOT invent prices, % moves, or dates. If unsure, write "data pending verification".
- No ticker symbols, price targets, buy/sell calls, or guaranteed returns.
- Full article in English Markdown. End with: *Disclaimer: For education only. Not investment advice.*
- Output the complete Markdown body directly — no outline-only, no "see attachment".
"""

_SLOT_PROMPTS: dict[str, str] = {
    "us_wrap": """You are a US markets editor for a 60-90 second finance Shorts channel.

Search the web for the **latest US stock market session** (S&P 500, Nasdaq, Dow). Write a **market wrap + plain-English analysis** (800-1400 words).

Structure:
# Title (question-style, e.g. "Why did the S&P 500 slip today?")

## What happened (bullet facts with numbers)
- Major index moves, biggest sector winners/losers, notable volume or VIX if relevant

## Why it moved (3 short points, everyday analogies OK)

## What to watch next (2-3 objective bullets, not trade advice)

Pick ONE clear narrative — do not list every headline.
""" + _COMMON_RULES,
    "mega_cap": """You are a US equities analyst for a Shorts audience.

Search the **hottest mega-cap story** in the last 48 hours (Magnificent Seven, AI leaders, earnings surprise, guidance change).

Write **one company-focused explainer** (900-1500 words):
1. Hook: why this name is on everyone's feed today
2. What happened (facts + numbers)
3. Explain the business in plain English (coffee-shop analogies OK)
4. Why the market reacted this way
5. Risks / what could change the story
6. One open question for viewers

Deep dive ONE company only — no ticker symbols in the title.
""" + _COMMON_RULES,
    "macro": """You are a macro strategist for retail investors.

Search the **top US macro story** in the last 72 hours (Fed, rates, CPI/PCE, jobs report, treasury yields, dollar, oil).

Write **one macro explainer** (900-1500 words):
1. Question-style title
2. One-sentence takeaway
3. What happened (who said/did what, when)
4. Transmission to stocks (rates → valuations, etc.) in plain English
5. What smart money is debating (both sides, no calls)
6. 3 things to watch next

One storyline only — not a news digest.
""" + _COMMON_RULES,
    "sector": """You are a sector strategist.

Search which **US equity sector or theme** is leading or lagging this week (tech, energy, financials, semis, biotech, etc.).

Write **one sector spotlight** (900-1500 words):
1. Why this sector is the story right now
2. Key numbers from the last session/week
3. Simple chain: macro → sector → example names (company names only, no tickers)
4. Is this a one-day pop or a trend? (balanced view)
5. Risks for the thesis
6. Closing question for viewers

Focus on ONE sector/theme.
""" + _COMMON_RULES,
}


def _us_history_path() -> Path:
    from locale_env import locale_logs_dir

    return locale_logs_dir("en") / "us_market_history.json"


def _last_slot_index() -> int:
    path = _us_history_path()
    if not path.is_file():
        return -1
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return -1
    for row in reversed(rows[-20:]):
        slot = str(row.get("slot") or row.get("theme_cluster") or "").strip()
        if slot in US_SLOT_ORDER:
            return US_SLOT_ORDER.index(slot)
    return -1


def _topic_for_slot(slot: str) -> dict:
    label = SLOT_LABEL[slot]
    today = date.today().isoformat()
    return {
        "slot": slot,
        "direction": "usmarket",
        "category": "usmarket",
        "title_hint": f"{today} {label}",
        "theme_cluster": f"us_{slot}",
        "angle": label,
        "reason": f"Cursor slot: {label}",
    }


def discover_us_cursor_topic(*, custom_hint: str = "") -> dict:
    """单条槽位选题（兼容旧调用）。"""
    topics = discover_us_cursor_topics(target=1, custom_hint=custom_hint)
    return topics[0] if topics else _topic_for_slot(US_SLOT_ORDER[0])


def discover_us_cursor_topics(*, target: int = 4, custom_hint: str = "") -> list[dict]:
    """固定槽位轮换选题列表（不调 Exa）。默认一轮 4 槽各 1 条。"""
    load_env()
    if custom_hint.strip():
        return [{
            "slot": "custom",
            "direction": "usmarket",
            "category": "usmarket",
            "title_hint": custom_hint.strip(),
            "cold_open": "",
            "theme_cluster": "custom",
            "angle": "user topic",
            "reason": "manual topic",
        }]
    target = max(1, min(target, len(US_SLOT_ORDER)))
    last = _last_slot_index()
    return [
        _topic_for_slot(US_SLOT_ORDER[(last + 1 + i) % len(US_SLOT_ORDER)])
        for i in range(target)
    ]


def topic_plan_for_slot(slot: str, *, topic: dict | None = None) -> dict:
    topic = topic or {}
    plan = {
        "slot": slot,
        "title_hint": topic.get("title_hint"),
        "cold_open": topic.get("cold_open"),
        "theme_cluster": topic.get("theme_cluster"),
        "angle": topic.get("angle"),
        "direction": "usmarket",
        "category": "usmarket",
    }
    return {k: v for k, v in plan.items() if v}


def _extract_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return re.sub(r"^#+\s*", "", line).strip()[:90] or fallback
    return fallback[:90]


def _save_draft(slot: str, markdown: str, meta: dict) -> Path:
    from locale_env import locale_logs_dir

    drafts = locale_logs_dir("en") / "us_cursor_drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = drafts / f"{stamp}_{slot}.md"
    header = (
        f"<!-- slot={slot} locale=en model={model_id()} "
        f"agent={meta.get('agent_id')} run={meta.get('run_id')} -->\n\n"
    )
    path.write_text(header + markdown.strip() + "\n", encoding="utf-8")
    path.with_suffix(".json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def run_cursor_draft(
    slot: str,
    *,
    custom_hint: str = "",
    agent_id: str | None = None,
    on_assistant=None,
) -> tuple[str, str, str]:
    if slot == "custom":
        if not custom_hint.strip():
            raise ValueError("custom slot requires topic hint")
        prompt = (
            f"【Today】{date.today().isoformat()}\n"
            f"【User topic】{custom_hint.strip()}\n\n"
            "Write an English finance explainer article (900-1500 words) for a US market Shorts video.\n"
            "Use web search for fresh facts. Question-style title, plain English, everyday analogies.\n"
            + _COMMON_RULES
        )
        label = f"custom: {custom_hint[:60]}"
    elif slot not in _SLOT_PROMPTS:
        raise ValueError(f"unknown slot: {slot}")
    else:
        label = SLOT_LABEL[slot]
        prompt = (
            f"【Today】{date.today().isoformat()}\n"
            f"【Task type】{label}\n\n"
            + _SLOT_PROMPTS[slot]
        )

    print(f"  ☁️  Cursor Cloud Agent · {label} · model={model_id()}", flush=True)
    if agent_id:
        run_id = create_run(agent_id, prompt)
        print(f"     reuse agent={agent_id} run={run_id}", flush=True)
    else:
        agent_id, run_id = create_agent(prompt)
        print(f"     new agent={agent_id} run={run_id}", flush=True)

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
        raise RuntimeError(f"Cursor returned empty body (slot={slot} status={status})")
    return body, agent_id, status


def build_us_cursor_research(
    topic: dict,
    *,
    agent_id: str | None = None,
) -> tuple[dict, dict, str | None]:
    """Cursor 英文草稿 → Opus 深读 → 返回 (article, details, agent_id)。"""
    slot = str(topic.get("slot") or "us_wrap").strip()
    custom = str(topic.get("title_hint") or "") if slot == "custom" else ""

    markdown, agent_id, status = run_cursor_draft(
        slot,
        custom_hint=custom,
        agent_id=agent_id,
    )
    if status != "FINISHED":
        print(f"  ⚠️  Agent status={status}, continuing with partial text", file=sys.stderr)

    meta = {
        "slot": slot,
        "status": status,
        "agent_id": agent_id,
        "model": model_id(),
        "locale": "en",
    }
    draft_path = _save_draft(slot, markdown, meta)
    print(f"  ✓ Cursor draft saved: {draft_path} ({len(markdown)} chars)")

    plan = topic_plan_for_slot(slot, topic=topic)
    fallback = str(topic.get("title_hint") or SLOT_LABEL.get(slot, slot))
    title = _extract_title(markdown, fallback)

    article = {
        "title": title,
        "url": f"cursor-draft://{draft_path.name}",
        "site": "cursor-cloud-agent",
        "author": model_id(),
        "published_at": date.today().isoformat(),
        "language": "en",
        "summary_en": markdown[:600],
        "thesis": title,
        "source_type": f"cursor:us_{slot}",
        "_cursor_draft": str(draft_path),
        "_no_source": True,
        "_topic_plan": plan,
    }

    print("  🤖 Opus deep-read Cursor draft (English)…")
    details = deep_read_us_markdown(article, markdown)
    return article, details, agent_id
