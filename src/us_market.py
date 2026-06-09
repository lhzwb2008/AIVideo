#!/usr/bin/env python3
"""US Market 英文热点分析：选题 → Exa 搜文 → 深读 → 改编 → 脚本 JSON。"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

from batch_aivideo import history_recent_topics
from paths import ROOT
from research import (
    _article_looks_ok,
    _dedup_results,
    _exa_result_to_candidate,
    _exa_search_pool,
    extract_json,
    load_env,
    max_slides,
    text_model,
)
from text_client import chat_complete

US_EXA_QUERIES = [
    "US stock market hot topic today S&P 500 Nasdaq analysis",
    "Wall Street market wrap stocks moved today why",
    "Federal Reserve interest rates stocks reaction analysis",
    "Magnificent Seven earnings stock market analysis this week",
    "sector rotation US stocks tech financials energy analysis",
    "US macro economy inflation jobs report stock market impact",
    "Nvidia Tesla Apple Microsoft Amazon Meta stock news analysis",
    "market volatility VIX treasury yields stocks explained",
]

US_PROPOSE_SYSTEM = """You are the daily editor for an English finance Shorts channel focused on US markets.

Input: recent US stock / macro news candidates (title + snippet).

Pick ONE hot topic worth a 60-90 second explainer video today.

Rules:
1. title_hint: English question-style hook, 30-80 chars. Examples: "Why did the S&P 500 drop today?" / "What does the Fed decision mean for tech stocks?"
2. cold_open: ONE punchy sentence, 40-90 chars. Start with a relatable hook (401k, mortgage, paycheck, grocery bill, phone bill) then pivot to the market move. No "Today we talk about…".
3. theme_cluster: snake_case id (fed_rates, mega_cap_earnings, sector_rotation, jobs_report, treasury_yields, ai_trade, general).
4. angle: 20-50 chars, the single lens for this video.
5. entity_name: company or macro actor if relevant (Nvidia, Fed, S&P 500…), else empty.
6. Prefer fresh news within the search window; no stock tips, price targets, buy/sell calls, or ticker symbols in title_hint/cold_open.
7. Avoid repeating recent topics/clusters provided below.

Output JSON only, no markdown."""

US_PROPOSE_USER = """【Today】{today}

【Recent video titles — do not repeat】
{recent_topics_json}

【Recent theme clusters — do not repeat】
{recent_clusters_json}

【News candidates】
{candidates_json}

Output:
{{
  "topic": {{
    "direction": "usmarket",
    "category": "usmarket",
    "entity_name": "",
    "title_hint": "Why did …?",
    "cold_open": "Relatable hook + market twist in one sentence.",
    "theme_cluster": "fed_rates",
    "angle": "single angle for this video",
    "reason": "20-60 chars on freshness"
  }}
}}"""

US_DEEP_READ_SYSTEM = """You are a financial research analyst preparing material for a short English explainer video.

Read the article thoroughly and extract every fact, number, quote, and analogy that could support a 3-4 slide script.

Output JSON in English. Translate non-English source material. Do not invent facts.

Required fields:
- thesis: one-sentence core takeaway
- outline: array of {{"heading", "summary", "key_numbers": []}}
- all_numbers: notable figures with context
- all_quotes: short quotes with attribution
- key_terms: jargon → plain English definitions
- everyday_analogies: ideas to explain concepts with coffee shop / rent / paycheck metaphors
- risks_and_caveats: what could change the story
- watch_next: 1-2 things to monitor

JSON only."""

US_DEEP_READ_USER = """【Article metadata】
Title: {title}
URL: {url}
Site: {site}
Date: {published_at}

【Full text】
{body}

Extract structured research details in English."""

US_ADAPT_PROMPT = """You write scripts for "Market Sketch" — a US finance Shorts channel in plain English.

Turn the research into a 3-4 slide vertical video script. Facts must come from the research only.

Visual style: hand-drawn comic explainer panels on graph paper (program adds style). Your image_prompt describes CONTENT only in English. on_image_text labels are short English phrases (2-5 words each).

Output a single JSON object:
{{
  "title": "English question title, 20-70 chars",
  "keyword": "2-30 char topic keyword",
  "cold_open": "40-90 chars, one sentence, relatable hook",
  "cold_open_type": "conflict|number|question|myth_bust",
  "theme_cluster": "snake_case",
  "angle": "20-50 chars",
  "hashtags": ["3-5 searchable tags, no brand name"],
  "slides": [
    {{
      "headline": "3-40 chars on-screen title",
      "narration": "cover 80-280 chars; body 100-420 chars",
      "image_prompt": "English scene description for comic panel",
      "on_image_text": ["short label", "short label", "short label"]
    }}
  ]
}}

Rules:
- 3-4 slides max; slide 1 is cover (do NOT repeat cold_open in slide 1 narration — start explaining).
- Last slide: one open question for comments + ask viewers to save the video for reference. No "like and subscribe" spam.
- Explain jargon with analogies (coffee, rent, paycheck, grocery).
- Tone: friendly teacher, not news anchor. No "the article says".
- Compliance: NO buy/sell/hold calls, price targets, guaranteed returns, or ticker symbols (NVDA, AAPL, etc.). Use company names only.
- hashtags: real search terms (#stocks #Fed #earnings #S&P500), not invented phrases.
- JSON only, no markdown."""

US_ADAPT_FIX = """Your previous JSON failed validation. Output the FULL corrected JSON only.

Errors:
{errors}

Article URL: {url}
Keep 3-4 slides, English throughout, compliance rules unchanged."""

_RECO_BANNED = re.compile(
    r"\b(buy now|sell now|must buy|guaranteed|price target|to the moon|"
    r"financial advice|you should buy|you should sell|get rich)\b",
    re.I,
)
_TICKER_RE = re.compile(r"\b[A-Z]{1,5}\b")
_TICKER_ALLOW = {"AI", "ETF", "GDP", "CPI", "PPI", "Fed", "SEC", "IPO", "CEO", "CFO", "US", "UK", "EU"}


def log(msg: str) -> None:
    print(msg, flush=True)


def _recent_clusters() -> list[str]:
    path = ROOT / "logs" / "us_market_history.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    clusters: list[str] = []
    for row in data[-30:]:
        c = str(row.get("theme_cluster") or "").strip()
        if c:
            clusters.append(c)
    return clusters


def _search_candidates(*, days: int) -> list[dict]:
    pool = _exa_search_pool(days=max(1, days), exclude_urls=None, queries=US_EXA_QUERIES)
    candidates = [_exa_result_to_candidate(r, language="en", source_type="exa:usmarket") for r in pool]
    return _dedup_results([c for c in candidates if _article_looks_ok(c)])[:40]


def discover_us_topic(*, days: int = 3) -> dict:
    """从 Exa 美股/宏观热点里选出 1 个英文话题。"""
    candidates = _search_candidates(days=days)
    if not candidates:
        raise RuntimeError("未找到可用的 US market 新闻候选")

    recent_topics = history_recent_topics()
    recent_clusters = _recent_clusters()
    user = US_PROPOSE_USER.format(
        today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        recent_topics_json=json.dumps(recent_topics[-20:], ensure_ascii=False),
        recent_clusters_json=json.dumps(recent_clusters[-15:], ensure_ascii=False),
        candidates_json=json.dumps(
            [{"title": c.get("title"), "url": c.get("url"), "site": c.get("site"), "summary": (c.get("summary_en") or c.get("summary_zh") or "")[:240]} for c in candidates[:25]],
            ensure_ascii=False,
        ),
    )
    raw = chat_complete(system=US_PROPOSE_SYSTEM, user=user, max_tokens=4000, response_format_json=True)
    data = extract_json(raw)
    topic = data.get("topic") if isinstance(data, dict) else None
    if not isinstance(topic, dict):
        raise RuntimeError(f"选题 JSON 缺少 topic: {data}")
    topic.setdefault("direction", "usmarket")
    topic.setdefault("category", "usmarket")
    return topic


def pick_article_for_topic(topic: dict, *, days: int = 3) -> dict:
    """按 title_hint 搜一篇最相关英文长文。"""
    hint = str(topic.get("title_hint") or "").strip()
    entity = str(topic.get("entity_name") or "").strip()
    query = f"{entity} {hint} US stock market analysis".strip()
    pool = _exa_search_pool(days=max(1, days), exclude_urls=None, queries=[query, *US_EXA_QUERIES[:3]])
    candidates = [_exa_result_to_candidate(r, language="en") for r in pool]
    candidates = _dedup_results([c for c in candidates if _article_looks_ok(c)])
    if not candidates:
        raise RuntimeError(f"搜不到相关文章: {hint}")
    article = candidates[0]
    article["_topic_plan"] = {k: topic.get(k) for k in ("title_hint", "cold_open", "theme_cluster", "angle", "direction", "category", "entity_name") if topic.get(k)}
    return article


def deep_read_us_article(article: dict) -> dict:
    import exa_client

    url = str(article.get("url") or "").strip()
    if not url:
        raise RuntimeError("文章缺少 URL")
    max_chars = int(os.environ.get("EXA_DEEP_READ_MAX_CHARS", "60000"))
    results = exa_client.get_contents([url], max_characters=max_chars)
    body = ""
    if results:
        body = str(results[0].get("text") or "").strip()
    if len(body) < 200:
        body = str(article.get("summary_en") or article.get("summary_zh") or article.get("thesis") or "").strip()
    if len(body) < 80:
        raise RuntimeError("原文过短，无法深读")

    user = US_DEEP_READ_USER.format(
        title=article.get("title", ""),
        url=url,
        site=article.get("site", ""),
        published_at=article.get("published_at", ""),
        body=body[:max_chars],
    )
    raw = chat_complete(system=US_DEEP_READ_SYSTEM, user=user, max_tokens=12000, response_format_json=True)
    details = extract_json(raw)
    if not isinstance(details, dict):
        raise RuntimeError("深读结果不是 object")
    details.setdefault("thesis", "")
    details.setdefault("outline", [])
    return details


def _check_ticker_free(text: str, *, field: str) -> None:
    for m in _TICKER_RE.finditer(text or ""):
        tok = m.group(0)
        if tok in _TICKER_ALLOW:
            continue
        if len(tok) <= 4 and tok.isupper():
            raise ValueError(f"{field} contains ticker-like token {tok!r} (use company names)")


def validate_us_script(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("root must be object")
    for key in ("title", "keyword", "cold_open", "slides"):
        if key not in data:
            raise ValueError(f"missing {key}")

    cold = str(data["cold_open"]).strip()
    if not (40 <= len(cold) <= 120):
        raise ValueError(f"cold_open must be 40-120 chars, got {len(cold)}")
    if _RECO_BANNED.search(cold):
        raise ValueError("cold_open contains banned recommendation language")

    title = str(data["title"]).strip()
    if len(title) < 12:
        raise ValueError("title too short")
    if len(title) > 90:
        data["title"] = title[:90].rstrip(" ,.")
    for field, val in (("title", title), ("cold_open", cold)):
        _check_ticker_free(val, field=field)
        if _RECO_BANNED.search(val):
            raise ValueError(f"{field} contains banned recommendation language")

    slides = data["slides"]
    limit = max_slides()
    if not isinstance(slides, list) or not (3 <= len(slides) <= limit):
        raise ValueError(f"slides count must be 3-{limit}")

    for i, slide in enumerate(slides):
        page = i + 1
        layout = "cover" if i == 0 else "body"
        slide["layout"] = layout
        for key in ("headline", "narration", "image_prompt"):
            if not str(slide.get(key) or "").strip():
                raise ValueError(f"slide {page} missing {key}")
        labels = slide.get("on_image_text") or []
        if not isinstance(labels, list) or not (3 <= len(labels) <= 10):
            raise ValueError(f"slide {page} on_image_text needs 3-10 items")
        n = str(slide["narration"]).strip()
        nlen = len(n)
        if layout == "cover":
            if not (80 <= nlen <= 320):
                raise ValueError(f"cover narration 80-320 chars, got {nlen}")
        else:
            if not (100 <= nlen <= 480):
                raise ValueError(f"slide {page} narration 100-480 chars, got {nlen}")
        _check_ticker_free(n, field=f"slide {page} narration")
        if _RECO_BANNED.search(n):
            raise ValueError(f"slide {page} narration has banned recommendation language")

    tags = data.get("hashtags") or []
    if not isinstance(tags, list) or not (3 <= len(tags) <= 6):
        raise ValueError("hashtags must be 3-6 items")
    data["category"] = "usmarket"
    data.setdefault("cold_open_type", "question")
    data.setdefault("theme_cluster", "general")
    data.setdefault("angle", "")
    return finalize_us_script(data)


def finalize_us_script(data: dict) -> dict:
    """补齐合成管线需要的 subtitle / lead_in / chapter_title 等字段。"""
    title = str(data.get("title") or "").strip()
    slides = data.get("slides")
    if not isinstance(slides, list):
        return data
    limit = max_slides()
    if len(slides) > limit:
        slides = slides[: limit - 1] + [slides[-1]]
        data["slides"] = slides
    for i, slide in enumerate(slides):
        if not isinstance(slide, dict):
            continue
        slide["layout"] = "cover" if i == 0 else "body"
        headline = str(slide.get("headline") or f"Part {i + 1}").strip()[:40]
        slide["headline"] = headline
        slide["chapter_title"] = str(slide.get("chapter_title") or ("Hook" if i == 0 else "Breakdown"))[:16]
        slide["concept"] = str(slide.get("concept") or headline)[:40]
        if i == 0:
            slide["subtitle"] = str(slide.get("subtitle") or headline or title)[:48]
        else:
            slide["lead_in"] = str(slide.get("lead_in") or headline)[:24]
        labels = slide.get("on_image_text") or []
        if not isinstance(labels, list):
            labels = []
        labels = [str(x).strip()[:24] for x in labels if str(x).strip()]
        while len(labels) < 3:
            labels.append(headline.split()[0] if headline else "Markets")
        slide["on_image_text"] = labels[:8]
    return data


def _merge_script(data: dict, article: dict) -> dict:
    data["source"] = {
        "title": article.get("title") or "",
        "url": article.get("url") or "",
        "site": article.get("site") or "",
    }
    data["article"] = article
    plan = article.get("_topic_plan")
    if isinstance(plan, dict):
        data["_topic_plan"] = plan
    return data


def adapt_us_script(article: dict, *, details: dict) -> dict:
    plan = article.get("_topic_plan") or {}
    user = (
        f"【Topic plan】\n{json.dumps(plan, ensure_ascii=False)}\n\n"
        f"【Article】\nTitle: {article.get('title')}\nURL: {article.get('url')}\n\n"
        f"【Research details】\n{json.dumps(details, ensure_ascii=False, indent=2)[:28000]}\n\n"
        "Adapt to English Shorts script JSON."
    )
    max_attempts = int(os.environ.get("ADAPT_MAX_ATTEMPTS", "5"))
    last_err: Exception | None = None
    last_parsed: dict | None = None
    for attempt in range(max_attempts):
        try:
            raw = chat_complete(
                system=US_ADAPT_PROMPT,
                user=user,
                max_tokens=int(os.environ.get("ADAPT_MAX_TOKENS", "12000")),
                response_format_json=True,
            )
            parsed = extract_json(raw, require_slides=True)
            last_parsed = parsed
            merged = _merge_script(parsed, article)
            return validate_us_script(merged)
        except (ValueError, json.JSONDecodeError) as exc:
            last_err = exc
            if attempt >= max_attempts - 1:
                break
            log(f"  ⚠️  adapt attempt {attempt + 1} failed: {exc}")
            fix = US_ADAPT_FIX.format(errors=str(exc), url=article.get("url", ""))
            if last_parsed:
                fix += "\n\nPrevious JSON:\n" + json.dumps(last_parsed, ensure_ascii=False)[:12000]
            user = f"{user}\n\n========\n{fix}"
    raise RuntimeError(f"US script adapt failed after {max_attempts} tries: {last_err}") from last_err


def build_us_research(topic: dict, *, days: int = 3) -> tuple[dict, dict]:
    article = pick_article_for_topic(topic, days=days)
    details = deep_read_us_article(article)
    return article, details


def run_us_research(
    *,
    output: str | os.PathLike[str],
    topic: dict,
    article: dict | None = None,
    details: dict | None = None,
    days: int = 3,
) -> dict:
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    if article is None or details is None:
        article, details = build_us_research(topic, days=days)
    log(f"  ✓ Article: {article.get('title')}")
    log(f"    {article.get('url')}")
    log(f"  ✓ Deep read: {len(details.get('outline') or [])} sections")
    script = adapt_us_script(article, details=details)
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "mode": "us_market",
        "locale": "en",
        "days": days,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "topic": topic,
        "article": article,
        "research_details": details,
        "script": script,
    }
    out_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return script


def append_us_history(script: dict, *, video: str = "") -> None:
    path = ROOT / "logs" / "us_market_history.json"
    rows: list[dict] = []
    if path.is_file():
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            rows = []
    rows.append({
        "title": script.get("title"),
        "theme_cluster": script.get("theme_cluster"),
        "keyword": script.get("keyword"),
        "video": video,
        "at": datetime.now(timezone.utc).isoformat(),
    })
    path.write_text(json.dumps(rows[-60:], ensure_ascii=False, indent=2), encoding="utf-8")
