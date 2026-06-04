#!/usr/bin/env python3
"""文章驱动的调研流水线（项目唯一管线）：

1. Exa/固定信息源搜索过去 N 天 AI 与财经热点 → Claude Opus 4.7 (low) 筛候选
2. Claude Opus 4.7 评审挑 1 篇
3. Exa /contents 取该文全文 → Claude Opus 4.7 抽出段落 outline / 数字 / 引语 / 场景 / 真实结尾
4. Claude Opus 4.7 基于深读细节改编为问句标题的 3-10 页中文短视频脚本

输出 schema 与 enrich_images.py / video_compose.py 兼容。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import exa_client
import feed_client
from paths import ROOT
from text_client import chat_complete, text_model


# ============================================================
# 通用工具
# ============================================================
def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def _unwrap_script(obj: dict) -> dict:
    """Agent 有时把脚本包在 script 字段里。"""
    if "slides" in obj:
        return obj
    inner = obj.get("script")
    if isinstance(inner, dict) and "slides" in inner:
        return inner
    return obj


def extract_json(text: str, *, require_slides: bool = False) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("Agent 返回为空")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    decoder = json.JSONDecoder()
    candidates: list[dict] = []
    idx = 0
    while idx < len(text):
        next_brace = text.find("{", idx)
        next_bracket = text.find("[", idx)
        if next_brace < 0 and next_bracket < 0:
            break
        if next_brace < 0:
            start = next_bracket
        elif next_bracket < 0:
            start = next_brace
        else:
            start = min(next_brace, next_bracket)
        try:
            obj, end = decoder.raw_decode(text, start)
            if isinstance(obj, dict):
                candidates.append(_unwrap_script(obj))
            elif isinstance(obj, list):
                candidates.append({"_array": obj})
            idx = end
        except json.JSONDecodeError:
            idx = start + 1
    if not candidates:
        raise ValueError("无法从 Agent 回复中解析 JSON")
    if require_slides:
        for obj in candidates:
            if isinstance(obj.get("slides"), list) and len(obj["slides"]) >= 1:
                return obj
        raise ValueError("回复中未找到含 slides 的完整脚本 JSON")
    for obj in candidates:
        if "slides" in obj:
            return obj
    return candidates[0]


def extract_topic_candidates(text: str) -> list[dict]:
    """从 Agent 回复中找候选数组（顶层数组，或包了一层的对象）。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        next_brace = text.find("{", idx)
        next_bracket = text.find("[", idx)
        if next_brace < 0 and next_bracket < 0:
            break
        if next_brace < 0:
            start = next_bracket
        elif next_bracket < 0:
            start = next_brace
        else:
            start = min(next_brace, next_bracket)
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            idx = start + 1
            continue
        idx = end
        if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
            return obj
        if isinstance(obj, dict):
            for key in ("candidates", "articles", "list"):
                v = obj.get(key)
                if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                    return v
    raise ValueError("Agent 未返回候选数组")


# ============================================================
# 阶段一：Exa 搜 + Opus 4.7 筛 3 篇候选
# ============================================================
EXA_QUERIES_EN = [
    "most discussed AI article this week long-form analysis",
    "Hacker News top AI story past week",
    "viral AI essay or report this week",
    "AI industry hot take or deep dive recent",
    "latest AI finance market analysis earnings stock reaction",
]

EXA_QUERIES_ZH = [
    "本周 AI 最热门 深度文章 长文",
    "AI 行业 头条 深度报道 一周",
    "大模型 最新进展 解读 深度",
    "人工智能 行业观察 评论长文",
    "AGI OR 大模型 OR 智能体 中文 深度",
    "AI 财经 美股 中概股 财报 分析 热点",
]

EXA_QUERIES_FINANCE = [
    "latest earnings analysis Magnificent Seven stocks revenue guidance stock reaction",
    "latest earnings analysis Chinese ADR Alibaba Tencent Baidu JD PDD NetEase Bilibili XPeng Li Auto NIO",
    "US stock market earnings analysis AI stocks today Nvidia Microsoft Meta Alphabet Amazon Tesla",
    "Reuters latest business finance earnings stock market AI companies",
    "Yahoo Finance latest earnings report AI stocks Chinese ADR",
    "Seeking Alpha latest earnings results AI stocks Chinese ADR",
    "最新 财报 分析 美股 七姐妹 英伟达 微软 苹果 Meta Google 亚马逊 特斯拉",
    "最新 中概股 财报 分析 阿里 腾讯 百度 京东 拼多多 网易 理想 蔚来 小鹏",
]

# A股个股专用：目标是吸引眼球、能引爆评论区的热点，不一定是最优质内容。
# 偏向个股异动、涨停/跌停、妖股、游资、人气榜、龙虎榜、业绩/公告突发。
EXA_QUERIES_ASTOCK_STOCK = [
    "A股 妖股 连板 游资 抱团 炒作 风口 人气股 热度榜",
    "财联社 A股 电报 异动 涨停 突发 龙虎榜 主力资金",
    "东方财富 股吧 人气股 热度榜 散户 抢筹 热门个股",
    "雪球 热门 A股 个股 讨论 大涨 大跌 业绩暴雷 黑马",
    "A股 个股 公告 利好 利空 业绩预增 业绩暴雷 重组 并购 减持",
    "A股 今日 个股 异动 涨停 跌停 龙虎榜 资金抢筹",
]

# A股板块/大盘专用：第二槽位才使用，避免第一条个股分析被板块热点稀释。
EXA_QUERIES_ASTOCK_SECTOR = [
    "A股 今日 涨停潮 题材 概念 板块 异动 资金抢筹",
    "同花顺 强势板块 题材归因 北向资金 主力净流入 概念板块",
    "A股 半导体 AI算力 机器人 算力 国产替代 大涨 龙头 炒作",
    "A股 上市 IPO 新股 暴涨 业绩预增 重组 并购 概念 爆发",
    "A股 大盘 指数 沪指 深成指 创业板 成交额 北向资金 行情 解读",
    "sina.com.cn OR cls.cn OR eastmoney.com A股 板块 大盘 今日热点 行情 解读",
]

EXA_QUERIES_ASTOCK = EXA_QUERIES_ASTOCK_STOCK + EXA_QUERIES_ASTOCK_SECTOR

EXA_QUERIES_HKUS_STOCK = [
    "latest earnings analysis Magnificent Seven stocks revenue guidance stock reaction",
    "latest earnings analysis Chinese ADR Alibaba Tencent Baidu JD PDD NetEase Bilibili XPeng Li Auto NIO",
    "US stock market individual stock earnings reaction Nvidia Microsoft Meta Alphabet Amazon Tesla",
    "Hong Kong US listed Chinese stocks earnings stock reaction latest analysis",
]

EXA_QUERIES_AI_NEWS = [
    "most discussed AI product launch model release this week analysis",
    "OpenAI Anthropic Google DeepMind Meta AI latest model product news analysis",
    "AI industry major funding partnership regulation product launch latest",
    "大模型 最新发布 AI 产品 产业 资讯 解读",
]

EXA_QUERIES_MACRO = [
    "Federal Reserve rates inflation jobs dollar yields market analysis latest",
    "global financial markets macro analysis central bank rates oil gold dollar latest",
    "international finance market analysis Fed ECB BOJ inflation bonds currencies",
    "美联储 降息 通胀 美债 美元 全球市场 国际金融 形势 分析",
]

EXA_TOPIC_SEARCH_TEMPLATES: dict[str, dict] = {
    "astock": {
        "label": "A股个股分析",
        "queries": EXA_QUERIES_ASTOCK_STOCK,
        "language": "zh",
        "source_type": "exa:astock",
        "days_cap": 3,
    },
    "sector": {
        "label": "A股板块和大盘",
        "queries": EXA_QUERIES_ASTOCK_SECTOR,
        "language": "zh",
        "source_type": "exa:astock",
        "days_cap": 3,
    },
    "hkus": {
        "label": "港美股个股分析",
        "queries": EXA_QUERIES_HKUS_STOCK,
        "language": "en",
        "source_type": "exa:finance",
    },
    "ai": {
        "label": "AI资讯",
        "queries": EXA_QUERIES_AI_NEWS,
        "language": "en",
        "source_type": "exa:finance",
    },
    "macro": {
        "label": "国际金融形势分析",
        "queries": EXA_QUERIES_MACRO,
        "language": "en",
        "source_type": "exa:finance",
    },
}

# 兼容旧名字（外部不再使用）
EXA_QUERIES = EXA_QUERIES_EN


def _site_from_url(url: str) -> str:
    m = re.match(r"https?://([^/]+)/?", url or "")
    host = m.group(1) if m else ""
    return host.replace("www.", "")


def _dedup_results(results: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for r in results:
        url = (r.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(r)
    return out


def _exa_search_pool(
    *, days: int, exclude_urls: list[str] | None,
    queries: list[str] | None = None,
) -> list[dict]:
    """跑多条 query 拉一大批 Exa 结果，去重 + 去 exclude，返回供 Opus 评审用的精简视图。"""
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00.000Z")
    excl = {u.strip() for u in (exclude_urls or []) if u.strip()}
    pool: list[dict] = []
    for q in (queries or EXA_QUERIES_EN):
        try:
            res = exa_client.search(
                q,
                num_results=10,
                start_published_date=start,
                summary_query="What is the article's main thesis, who wrote it, and why is it getting attention?",
                highlights_sentences=3,
            )
        except RuntimeError as e:
            print(f"  ⚠️  Exa 搜索失败「{q}」: {e}", file=sys.stderr)
            continue
        print(f"  🔍 Exa「{q}」→ {len(res)} 条")
        pool.extend(res)
    pool = _dedup_results(pool)
    pool = [r for r in pool if (r.get("url") or "").strip() not in excl]
    return pool


def _format_pool_for_opus(pool: list[dict]) -> str:
    view = []
    for r in pool[:40]:
        view.append({
            "title": r.get("title") or "",
            "url": r.get("url") or "",
            "site": _site_from_url(r.get("url") or ""),
            "author": r.get("author") or "",
            "published_at": (r.get("publishedDate") or "")[:10],
            "summary": r.get("summary") or "",
            "highlights": r.get("highlights") or [],
        })
    return json.dumps(view, ensure_ascii=False, indent=2)


def _exa_result_to_candidate(
    r: dict, *, language: str = "en", source_type: str = "exa:finance",
) -> dict:
    title = str(r.get("title") or "").strip()
    url = str(r.get("url") or "").strip()
    summary = str(r.get("summary") or title).strip()
    highlights = [str(x).strip() for x in (r.get("highlights") or []) if str(x).strip()]
    facts = highlights[:4] or [summary or title]
    is_astock = source_type == "exa:astock"
    return {
        "title": title,
        "url": url,
        "site": _site_from_url(url),
        "author": r.get("author") or "",
        "published_at": (r.get("publishedDate") or "")[:10],
        "language": language,
        "summary_en": summary if language == "en" else "",
        "summary_zh": summary if language == "zh" else title,
        "thesis": summary or title,
        "key_facts": facts,
        "narrative_arc": (
            "热点爆发 → 资金/题材逻辑 → 后市看点" if is_astock
            else "最新财经资讯 → 核心数据 → 市场影响"
        ),
        "heat_score": 9 if is_astock else 7,
        "heat_evidence": "Exa A股爆点搜索命中（最高优先）" if is_astock else "Exa 财经搜索命中",
        "estimated_pages": 5,
        "source_type": source_type,
    }


PICK_CANDIDATES_SYSTEM = """你是「AI财知道」选题总编。给你一批 Exa 搜回来的候选文章（含标题/URL/站点/日期/摘要/亮点片段），请按 AI 与财经圈真实热度挑出 **{n} 篇** 适合改编为短视频问答的{lang_label}长文/深度报道。

栏目定位：用大白话回答「AI 和财经类十万个为什么」。优先选择能被概括成一个搜索型问句的热点，例如「什么是 X」「X 为什么大涨」「X 财报到底好不好」「X 对普通人有什么影响」。

挑选标准（按重要性）：
1) AI、财经、美股、中概股、**A股** 全网真实热度（HN 高分、X 多人转、多家媒体同步报道、Reddit/Newsletter 头条、知乎/微博/即刻热门、公众号 10w+、东方财富股吧/雪球/同花顺人气榜高热度等）。尤其关注大型科技股/中概股财报、股价异动、宏观数据和监管事件。
   · **A股 爆品特别说明**：A股 部分以「爆品」为目标——优先选最能吸引眼球、能引爆评论区的热点（涨停潮、连板妖股、游资抱团、龙虎榜、人气股榜、题材爆炒、风口概念、业绩暴雷/暴增、突发利空利好），**不一定要是质量最高/最深度的内容，但必须够热够有话题性**。财联社电报、东方财富、同花顺、雪球、新浪财经里散户最关注、转发讨论最多的那种就是首选。
2) 自带完整叙事或核心观点，能讲清「这是什么、为什么重要、影响谁」；纯产品发布稿、纯参数更新、纯公关博客 pass。
3) 必须是 N 件不同的事；同一事件的多家报道只留最权威/最热那一版。
4) 必须是真实可访问的{lang_label}文章 URL，不是推文/视频。

★ 反作弊 · 真实新鲜度校验（极其重要）：
- Exa 返回的 `published_at` **未必是首发时间**，它可能是文章被重新索引、聚合、转载、被外链页带出的时间，会把几个月甚至一年前的旧文显示成"本周"。
- 你必须把 `published_at` 与 summary / highlights / 标题里出现的**事件时间线索**交叉验证：
    · summary 里出现"去年下半年"、"今年 X 月"、"几个月前"、"X 年发布"、"2024 / 2025 / 2026" 等时间表述时，要算出事件真实发生时间；
    · 若候选标题/摘要描述的是**早已发生过的事件**（如某模型在去年发布，但 published_at 写成本周），判定为**索引滞后/旧文重发**，整条**直接舍弃**，不要纳入候选；
    · 当前真实日期会在用户消息里告诉你，请以它为锚点判断"新鲜"。
- 宁可少返几篇，也不要把旧文当新热点放进去。

只输出严格 JSON 数组，长度恰好 {n}。"""

PICK_CANDIDATES_USER = """【当前真实日期】{today}（请以此为锚点判断候选的真实新鲜度，凡 published_at 与摘要里事件时间矛盾的一律舍弃）

以下是 Exa {lang_label}候选池（JSON 数组）：

{pool_json}

请挑 {n} 篇，按下面 schema 输出（**只输出 JSON 数组，不要 markdown，不要解释**）。所有字段都基于上面池子里那条记录的 summary/highlights/标题归纳，**不要编造**池里没有的数字或人名。

[
  {{
    "title": "原文标题（保留原文语言，不要翻译）",
    "url": "https://...",
    "site": "站点/作者",
    "author": "作者（若有，无则空串）",
    "published_at": "YYYY-MM-DD",
    "language": "{lang_code}",
    "summary_en": "2-3 句英文摘要（≤80 词，原文中文也用英文写）",
    "summary_zh": "中文一句话概括（25-50 字）",
    "thesis": "文章在讲一件什么事 / 核心观点（一句话）",
    "key_facts": ["从 summary/highlights 抽 3-6 个最硬的事实/数字/场景，每条 ≤25 字"],
    "narrative_arc": "文章自身的叙事节奏（一句话）",
    "heat_score": 1-10,
    "heat_evidence": "为什么热（站点权重 + 时间 + summary 暗示，≤30 字）",
    "estimated_pages": 5
  }},
  ... 共 {n} 条
]
{exclude_section}"""


def _article_looks_ok(c: dict) -> bool:
    if not isinstance(c, dict):
        return False
    if not str(c.get("url") or "").startswith("http"):
        return False
    for key in ("title", "site", "summary_zh", "thesis", "key_facts"):
        if not c.get(key):
            return False
    facts = c.get("key_facts") or []
    return isinstance(facts, list) and len(facts) >= 2


def _pick_from_pool(
    pool: list[dict],
    *,
    n: int,
    lang_code: str,
    lang_label: str,
    exclude_urls: list[str] | None,
    recent_topics: list[str] | None = None,
) -> list[dict]:
    """让 Opus 在给定（中文或英文）池里挑 n 篇候选。"""
    if not pool:
        return []
    print(f"  📥 Exa {lang_label}池共 {len(pool)} 条（去重去 exclude 后），让 {text_model()} 筛 {n} 篇…")
    exclude_section = ""
    if exclude_urls:
        joined = "\n  - ".join(exclude_urls)
        exclude_section = f"\n【硬性排除 URL】不要再选这些 URL：\n  - {joined}"
    if recent_topics:
        joined_t = "\n  - ".join(recent_topics)
        exclude_section += (
            f"\n【近期已做过的主题】下列主题/事件**最近已经做过视频**，"
            f"即便候选 URL 不同也要规避（同一主角/同一事件/同一发布的不同媒体复述都算重复）：\n  - {joined_t}"
        )
    user_msg = PICK_CANDIDATES_USER.format(
        pool_json=_format_pool_for_opus(pool),
        exclude_section=exclude_section,
        n=n,
        lang_label=lang_label,
        lang_code=lang_code,
        today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )
    raw = chat_complete(
        system=PICK_CANDIDATES_SYSTEM.format(n=n, lang_label=lang_label),
        user=user_msg,
        max_tokens=4000,
    )
    try:
        candidates = extract_topic_candidates(raw)
    except ValueError as e:
        print(f"  ⚠️  {lang_label}候选解析失败: {e}", file=sys.stderr)
        return []
    for c in candidates:
        if isinstance(c, dict) and not c.get("language"):
            c["language"] = lang_code
    return [c for c in candidates if _article_looks_ok(c)][:n]


def find_articles(
    *,
    days: int = 7,
    exclude_urls: list[str] | None = None,
    agent_id: str | None = None,
    per_lang: int = 3,
    recent_topics: list[str] | None = None,
    source: str = "exa",
    fresh_hours: int = 24,
    focus_directions: list[str] | tuple[str, ...] | None = None,
) -> tuple[list[dict], str | None]:
    """获取候选文章。默认固定信息源最近 24h；Exa 保留为兜底。"""
    if source == "feeds":
        excl = {u.strip() for u in (exclude_urls or []) if u.strip()}
        candidates = [
            c for c in feed_client.fetch_feed_candidates(hours=fresh_hours)
            if str(c.get("url") or "").strip() not in excl
        ]
        try:
            finance_pool = _exa_search_pool(
                days=max(1, days),
                exclude_urls=exclude_urls,
                queries=EXA_QUERIES_FINANCE,
            )
            finance_candidates = [
                _exa_result_to_candidate(r, language="en")
                for r in finance_pool
                if str(r.get("url") or "").strip() not in excl
            ][:30]
            if finance_candidates:
                print(f"  ✓ Exa 财经补源：{len(finance_candidates)} 篇")
                candidates.extend(finance_candidates)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️  Exa 财经补源失败：{exc}", file=sys.stderr)
        try:
            astock_pool = _exa_search_pool(
                days=max(1, min(days, 3)),
                exclude_urls=exclude_urls,
                queries=EXA_QUERIES_ASTOCK,
            )
            astock_candidates = [
                _exa_result_to_candidate(r, language="zh", source_type="exa:astock")
                for r in astock_pool
                if str(r.get("url") or "").strip() not in excl
            ][:30]
            if astock_candidates:
                print(f"  ✓ Exa A股爆点补源：{len(astock_candidates)} 篇")
                candidates.extend(astock_candidates)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️  Exa A股补源失败：{exc}", file=sys.stderr)
        candidates = _dedup_results(candidates)
        if not candidates:
            raise RuntimeError("固定信息源没有抓到候选")
        print(f"  ✓ 固定信息源候选：{len(candidates)} 篇（近 {fresh_hours} 小时，含财经补源）")
        return candidates, agent_id

    focus = [str(x).strip().lower() for x in (focus_directions or []) if str(x).strip()]
    templates = [
        EXA_TOPIC_SEARCH_TEMPLATES[key]
        for key in focus
        if key in EXA_TOPIC_SEARCH_TEMPLATES
    ]
    if not templates:
        templates = [
            {
                "label": "通用财经/AI",
                "queries": EXA_QUERIES_FINANCE + EXA_QUERIES_EN + EXA_QUERIES_ZH,
                "language": "en",
                "source_type": "exa:finance",
            },
            {
                "label": "A股爆点",
                "queries": EXA_QUERIES_ASTOCK,
                "language": "zh",
                "source_type": "exa:astock",
                "days_cap": 3,
            },
        ]

    valid: list[dict] = []
    seen_urls: set[str] = set()
    for tpl in templates:
        tpl_days = days
        if tpl.get("days_cap"):
            tpl_days = max(1, min(days, int(tpl["days_cap"])))
        print(f"  ▶ 搜索模板：{tpl['label']}")
        pool = _exa_search_pool(
            days=tpl_days,
            exclude_urls=exclude_urls,
            queries=list(tpl["queries"]),
        )
        rows = [
            _exa_result_to_candidate(
                r,
                language=str(tpl.get("language") or "en"),
                source_type=str(tpl.get("source_type") or "exa:finance"),
            )
            for r in pool
            if str(r.get("url") or "").strip() not in seen_urls
        ]
        for row in rows:
            url = str(row.get("url") or "").strip()
            if url:
                seen_urls.add(url)
        if rows:
            print(f"  ✓ {tpl['label']}候选：{len(rows)} 篇")
            valid.extend(rows)

    if not valid:
        raise RuntimeError("Exa 候选文章均不合规")
    print(f"  ✓ Exa 候选合并：{len(valid)} 篇")
    return valid, agent_id


def _print_candidates(candidates: list[dict]) -> None:
    print()
    print("=" * 72)
    print(f"  候选长文（{len(candidates)} 篇，中英混合）")
    print("=" * 72)
    for i, c in enumerate(candidates, 1):
        lang = (c.get('language') or '?').upper()
        print(f"\n[{i}][{lang}] {c.get('title')}")
        print(f"    站点    : {c.get('site')}  作者: {c.get('author') or '-'}  日期: {c.get('published_at') or '-'}")
        print(f"    一句话  : {c.get('summary_zh')}")
        print(f"    论点    : {c.get('thesis')}")
        print(f"    叙事    : {c.get('narrative_arc')}")
        print(f"    建议页数: {c.get('estimated_pages')}")
        if c.get("heat_score") is not None:
            print(f"    热度分  : {c.get('heat_score')}/10  — {c.get('heat_evidence') or ''}")
        facts = c.get("key_facts") or []
        if facts:
            print(f"    硬事实  :")
            for f in facts[:6]:
                print(f"        · {f}")
        print(f"    URL     : {c.get('url')}")
    print()


SCORE_TOPICS_SYSTEM = """你是抖音栏目「AI财知道」的选题打分器。你的任务不是 6 选 1，而是给每一个候选话题独立打分，凡是分数足够高的话题都应该进入待生成队列。

栏目定位：AI 和财经类「十万个为什么」。优先选择 24 小时内的新鲜事件，能用一个搜索型问句讲清楚「这是什么、为什么重要、会影响谁」。

★★ 最高优先级：A股「爆品」是本栏目的**第一权重**，高于美股、高于中概股 ★★
- 当一条 A股 爆点候选与美股/中概股候选热度、可讲性相近时，**永远优先选 A股**，并给它更高的 topic_score。
- A股 部分以「爆品」为目标——**优先吸引眼球、有话题性、能引爆评论区，不要求是质量最高或最深度的内容**。

打分标准（topic_score 0-100）：
- 95-100：必须做。**首选 A股 爆点（涨停潮/妖股连板/龙虎榜大战/游资抱团/人气股榜前排/题材总爆发/重磅 IPO 暴涨/业绩暴雷暴增/突发利空利好），只要真实新鲜即可进此档**；其次才是七姐妹（Apple、Microsoft、Nvidia、Alphabet/Google、Amazon、Meta、Tesla）或重点中概股（阿里、腾讯、百度、京东、拼多多、网易、携程、贝壳、B站、理想、蔚来、小鹏等）的最新财报/财报后股价大幅异动，或全市场级 AI/宏观/美股事件。
- 85-94：强烈建议做。A股 当日高热度题材/概念板块异动、人气股榜个股、游资炒作、热门 IPO/重组；或 AI 巨头战略、AI 商业化拐点、重要模型/产品发布、重要监管/利率/汇率/通胀数据、美股/港股/中概股核心资产明显异动。
- 75-84：可以做。有清晰事实、数字、冲突和搜索关键词，能讲成一个「为什么/是什么/意味着什么」。
- 60-74：备选。信息有价值但热度或可讲性一般。
- 0-59：不做。软文、重复、信息太薄、旧闻、标题党、缺少事实。

★ A股「爆品」专项规则（极重要）：
- 凡涉及 涨停/连板/妖股/游资/龙虎榜/人气股榜/题材爆炒/风口概念/业绩暴雷或暴增/突发利空利好/重磅 IPO 暴涨 的 A股 候选，只要事件真实且新鲜（当天或最近几天），就**给到最高档加分**，哪怕来源只是快讯/股吧热帖。
- A股 爆点天然带「为什么大涨/为什么大跌/谁在炒/能不能追」这类强搜索问句，可讲性极高，应优先进入待生成队列。
- 但仍要规避纯荐股、纯喊单、明显违规的「内幕消息」类内容；这类标记 opinion_risk=true 并降分。

高分加权（从高到低）：
- 【最高】A股 涨停潮/妖股/龙虎榜/人气榜/题材爆炒/重磅 IPO 暴涨/业绩暴雷暴增：爆品第一优先，权重高于下面所有项。
- 七姐妹和重点中概股的最新财报分析、earnings、results、guidance、revenue、profit、EPS、业绩、指引、电话会：显著加分（但低于 A股 爆品）。
- 同时具备 AI + 财经/股价/财报属性：加分。
- 财联社/华尔街见闻/东方财富/同花顺/雪球/新浪财经/Reuters/Yahoo Finance/Seeking Alpha 等可信或高人气财经源：加分，但 Seeking Alpha 和个人观点要标记 opinion_risk。

去重规则：
- 如果与近期已做过标题是同一主角、同一事件、同一发布、同一财报，即使 URL 不同也应 marked duplicate=true，topic_score 不得超过 40。
- 同一事件多篇报道只保留信息密度最高、最权威的一条，其余 marked duplicate=true。

输出严格 JSON。"""

SCORE_TOPICS_USER = """【当前真实日期】{today}（请用它作为新鲜度锚点；凡事件实际时间距今超过 60 天的候选都视为旧文，无论 published_at 写得多新，都明显降分）

【近期已做过的标题】
{recent_topics_json}

【候选资讯】
请逐条打分，不要只选一个。

{candidates_json}

只输出一个 JSON 对象（不要 markdown，不要解释）：
{{
  "scored": [
    {{
      "index": 1-based 整数,
      "topic_score": 0-100,
      "priority": "must|high|medium|low|reject",
      "question_title": "适合视频的中文问句标题",
      "reason": "25-60字，说明为什么这个分数",
      "duplicate": true/false,
      "duplicate_reason": "如重复，说明与哪个历史标题或候选重复；不重复则空串",
      "category": "ai|finance|earnings|macro|stock|astock|mixed",
      "opinion_risk": true/false
    }}
  ]
}}"""


def score_articles(
    candidates: list[dict],
    *,
    recent_topics: list[str] | None = None,
) -> tuple[list[dict], dict]:
    """用 Opus 给候选逐条打分（遗留 API；主流程已改用 daily_topics 问句话题选题）。"""
    max_candidates = int(os.environ.get("AIVIDEO_SCORE_MAX_CANDIDATES", "40"))
    cand_view = []
    for i, c in enumerate(candidates, 1):
        if len(cand_view) >= max_candidates:
            break
        summary = str(c.get("summary_zh") or c.get("summary_en") or c.get("thesis") or "")
        thesis = str(c.get("thesis") or "")
        facts = [str(x)[:80] for x in (c.get("key_facts") or [])[:3]]
        cand_view.append({
            "index": i,
            "title": str(c.get("title") or "")[:160],
            "site": c.get("site"),
            "summary_zh": summary[:220],
            "thesis": thesis[:220],
            "key_facts": facts,
            "narrative_arc": c.get("narrative_arc"),
            "heat_score": c.get("heat_score"),
            "heat_evidence": c.get("heat_evidence"),
            "published_at": c.get("published_at"),
            "url": c.get("url"),
            "source_type": c.get("source_type"),
        })
    user_msg = SCORE_TOPICS_USER.format(
        candidates_json=json.dumps(cand_view, ensure_ascii=False, indent=2),
        recent_topics_json=json.dumps(recent_topics or [], ensure_ascii=False, indent=2),
        today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )
    print(f"  🤖 让 {text_model()} 给 {len(candidates)} 篇候选逐条打分…")
    raw = chat_complete(
        system=SCORE_TOPICS_SYSTEM,
        user=user_msg,
        max_tokens=5000,
        response_format_json=True,
    )
    decision = extract_json(raw)
    threshold = int(os.environ.get("AIVIDEO_TOPIC_SCORE_THRESHOLD", "75"))
    scored = decision.get("scored") or []
    enriched: list[dict] = []
    all_scored: list[dict] = []
    non_duplicate: list[dict] = []
    for row in scored:
        try:
            idx = int(row.get("index") or 0)
            score = int(row.get("topic_score") or 0)
        except (TypeError, ValueError):
            continue
        if not (1 <= idx <= len(candidates)):
            continue
        cand = dict(candidates[idx - 1])
        cand["_candidate_index"] = idx
        cand["topic_score"] = score
        cand["priority"] = row.get("priority") or ""
        cand["question_title"] = row.get("question_title") or ""
        cand["score_reason"] = row.get("reason") or ""
        cand["duplicate"] = bool(row.get("duplicate"))
        cand["duplicate_reason"] = row.get("duplicate_reason") or ""
        cand["category"] = row.get("category") or ""
        cand["opinion_risk"] = bool(row.get("opinion_risk"))
        all_scored.append(cand)
        if cand["duplicate"]:
            continue
        non_duplicate.append(cand)
        if score >= threshold:
            enriched.append(cand)
    enriched.sort(key=lambda c: int(c.get("topic_score") or 0), reverse=True)
    non_duplicate.sort(key=lambda c: int(c.get("topic_score") or 0), reverse=True)
    all_scored.sort(key=lambda c: int(c.get("topic_score") or 0), reverse=True)
    if not enriched and os.environ.get("AIVIDEO_STRICT_SCORE_THRESHOLD", "0") != "1":
        best = (non_duplicate or all_scored or [])[0] if (non_duplicate or all_scored) else None
        if best is None:
            raise RuntimeError("模型没有返回可用的 scored 候选")
        print(
            f"  ⚠️  没有候选达到阈值 {threshold}，兜底采用最高分 {best.get('topic_score')}：{best.get('title')}",
            file=sys.stderr,
        )
        enriched = [best]
    decision["threshold"] = threshold
    decision["accepted_count"] = len(enriched)
    decision["fallback_used"] = bool(enriched and int(enriched[0].get("topic_score") or 0) < threshold)
    decision["ranking"] = [int(c.get("_candidate_index") or 0) for c in enriched if c.get("_candidate_index")]
    print(f"  ✓ 打分完成：{len(enriched)} 篇达到阈值 {threshold}")
    for c in enriched[:10]:
        print(f"    [{c.get('topic_score')}] {c.get('title')} — {c.get('score_reason')}")
    if not enriched:
        raise RuntimeError(f"没有候选达到 topic_score 阈值 {threshold}")
    return enriched, decision


def pick_article(
    candidates: list[dict],
    *,
    auto: bool = False,
    recent_topics: list[str] | None = None,
) -> tuple[dict, dict | None]:
    _print_candidates(candidates)
    if auto:
        scored, decision = score_articles(candidates, recent_topics=recent_topics)
        return scored[0], decision
    while True:
        raw = input(f"请输入 1-{len(candidates)}（回车=1）: ").strip() or "1"
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(candidates):
                return candidates[idx - 1], None
        print(f"  ✗ 无效，请输入 1-{len(candidates)}")


# ============================================================
# 阶段 1.5：Cursor 深度读文章，把所有细节捞回来给 Opus
# ============================================================
DEEP_READ_SYSTEM = """你是 AI 内容研究员。用户消息会给你一篇文章的全文（中文或英文，由 Exa 抓取）。请把它完整读一遍，再把所有可能用于短视频改编的素材**穷尽式**地抽取出来。最终所有字段都用**中文**输出（如果原文是英文，translate；如果原文是中文，直接转写）。

所有字段**必须基于用户消息里给出的原文**，不准虚构、不准联想、不准引入外部知识。只输出严格 JSON。"""


DEEP_READ_USER_TEMPLATE = """【目标文章】
- 标题: {title}
- 站点: {site}  作者: {author}  日期: {published_at}
- URL: {url}
- 已知一句话: {summary_zh}

【原文全文（Exa 抓取，可能含少量噪声字符，请按内容理解）】
<<<ARTICLE_BEGIN
{full_text}
ARTICLE_END>>>

【任务】
基于上面的原文全文输出一个 JSON 对象（不要 markdown，不要解释）。

【硬性要求】
- `outline`：原文段落级 outline，**每段一行中文概括**（≤30 字），按原文出现顺序排列；至少 8 行（如果原文很短且自然分段少于 8 段，给原文真实段数）。
- `all_numbers`：原文里出现的**每一个**具体数字 / 金额 / 比例 / 时间，附上下文。哪怕看起来没用也写下来。
- `all_quotes`：原文里**值得被引用的句子或人名引语**（5-15 条），每条带说话人/作者；翻成中文，但保留关键英文词。
- `people`：文中出现的所有具体人名，附身份/职位。
- `companies_or_institutions`：所有公司、实验室、机构。
- `key_terms`：原文出现的术语，每个用一句中文白话解释（≤25 字），适合给小白听。
- `concrete_scenes`：原文里**具体的场景/事件画面**（"X 在 Y 时做了 Z"格式），3-8 条，越具体越好。
- `everyday_analogies`：针对文中最难懂的概念/数字/逻辑，提出 3-6 个**生活化类比或例子**（用买菜、点外卖、租房、打车、开奶茶店、追剧等普通人熟悉的事来打比方），格式「X 就好比 Y」，帮零基础观众秒懂。可基于常识合理类比，但不得歪曲原文事实。
- `actual_opening`：原文真实的开头第一段中文转写（≤80 字，原汁原味，不要总结）。
- `actual_ending`：原文真实的最后一段或结论句中文转写（≤120 字）。
- `narrative_beats`：作者真实的叙事节奏（5-10 拍：「先...，然后...，转折是...，最后...」），每拍一句。
- `author_stance`：作者**自己的态度/立场**一句话（不是他描述别人的观点，是他自己怎么想）。
- `omit_in_video`：你建议改编短视频时**砍掉不讲**的内容（哪些段落对普通观众没意义），3-5 条。

【输出】
只输出一个 JSON 对象，键名严格按上面列。不要 markdown，不要解释。
"""


_DEEP_READ_MAX_CHARS = int(os.environ.get("EXA_DEEP_READ_MAX_CHARS", "60000"))


def _fetch_article_text(url: str) -> str:
    print(f"  📥 Exa 抓全文 {url}")
    results = exa_client.get_contents([url], max_characters=_DEEP_READ_MAX_CHARS)
    if not results:
        raise RuntimeError(f"Exa /contents 没返回任何结果: {url}")
    text = (results[0].get("text") or "").strip()
    if len(text) < 500:
        raise RuntimeError(f"Exa 抓回的正文过短 ({len(text)} 字): {url}")
    print(f"  ✓ 全文 {len(text)} 字符")
    return text


def _fallback_details_from_article(article: dict) -> dict:
    """信息源只有标题/摘要时的轻量细节，避免 daily 因抓全文失败中断。"""
    title = str(article.get("title") or "").strip()
    summary = str(article.get("summary_zh") or article.get("summary_en") or article.get("thesis") or title).strip()
    facts = [str(x).strip() for x in (article.get("key_facts") or []) if str(x).strip()]
    if not facts:
        facts = [summary or title]
    outline = [title] + facts[:6]
    return {
        "outline": outline,
        "all_numbers": [x for x in facts if re.search(r"\d", x)] or facts[:2],
        "all_quotes": [],
        "people": [],
        "companies_or_institutions": [],
        "key_terms": [title[:25]],
        "concrete_scenes": facts[:5],
        "actual_opening": summary[:80] or title[:80],
        "actual_ending": summary[-120:] if summary else title,
        "narrative_beats": [article.get("narrative_arc") or "最新事件发布，然后分析关键影响。"],
        "author_stance": summary or title,
        "omit_in_video": [],
        "_fallback": True,
    }


def _normalize_deep_read_details(details: dict, article: dict) -> dict:
    """补齐深读 JSON 的必需字段，避免模型漏字段导致整条选题报废。"""
    fallback = _fallback_details_from_article(article)
    if not isinstance(details, dict):
        return fallback
    for key, value in fallback.items():
        if not details.get(key):
            details[key] = value
    return details


def deep_read_article(
    article: dict,
    *,
    agent_id: str | None,
    full_text: str | None = None,
) -> tuple[dict, str | None]:
    """Exa 抓全文 → Opus 4.7 抽细节，返回细节字典。

    full_text 给定时直接用它当原文（指定话题模式下用户自带内容/已搜回来的正文），
    跳过 Exa /contents 抓取。
    """
    url = str(article.get("url") or "")
    if full_text is None:
        if article.get("source_type") == "feed:aibase" and url.rstrip("/") == "https://www.aibase.com/zh/news":
            print("  📥 AIbase 首页条目无单篇静态 URL，使用标题/摘要轻量细节")
            return _fallback_details_from_article(article), agent_id
        try:
            full_text = _fetch_article_text(url)
        except RuntimeError as exc:
            if str(article.get("source_type") or "").startswith("feed:"):
                print(f"  ⚠️  抓全文失败，使用信息源摘要兜底：{exc}", file=sys.stderr)
                return _fallback_details_from_article(article), agent_id
            raise
    else:
        full_text = full_text.strip()
        print(f"  📥 使用给定原文（{len(full_text)} 字符），跳过 Exa 抓取")
    user_msg = DEEP_READ_USER_TEMPLATE.format(
        title=article.get("title", ""),
        site=article.get("site", ""),
        author=article.get("author") or "-",
        published_at=article.get("published_at") or "-",
        url=article.get("url", ""),
        summary_zh=article.get("summary_zh", ""),
        full_text=full_text,
    ) + """

【硬性字段要求】
- `outline`：原文段落级 outline，**每段一行中文概括**（≤30 字），按原文出现顺序排列；至少 8 行（如果原文很短且自然分段少于 8 段，给原文真实段数）。
- `all_numbers`：原文里出现的**每一个**具体数字 / 金额 / 比例 / 时间，附上下文。哪怕看起来没用也写下来。
- `all_quotes`：原文里**值得被引用的句子或人名引语**（5-15 条），每条带说话人/作者；翻成中文，但保留关键英文词。
- `people`：文中出现的所有具体人名，附身份/职位。
- `companies_or_institutions`：所有公司、实验室、机构。
- `key_terms`：原文出现的术语，每个用一句中文白话解释（≤25 字），适合给小白听。
- `concrete_scenes`：原文里**具体的场景/事件画面**（"X 在 Y 时做了 Z"格式），3-8 条，越具体越好。
- `everyday_analogies`：针对文中最难懂的概念/数字/逻辑，提出 3-6 个**生活化类比或例子**（用买菜、点外卖、租房、打车、开奶茶店、追剧等普通人熟悉的事来打比方），格式「X 就好比 Y」，帮零基础观众秒懂。可基于常识合理类比，但不得歪曲原文事实。
- `actual_opening`：原文真实的开头第一段中文转写（≤80 字，原汁原味，不要总结）。
- `actual_ending`：原文真实的最后一段或结论句中文转写（≤120 字）。
- `narrative_beats`：作者真实的叙事节奏（5-10 拍：「先...，然后...，转折是...，最后...」），每拍一句。
- `author_stance`：作者**自己的态度/立场**一句话（不是他描述别人的观点，是他自己怎么想）。
- `omit_in_video`：你建议改编短视频时**砍掉不讲**的内容（哪些段落对普通观众没意义），3-5 条。

只输出一个 JSON 对象，键名严格按上面列。"""
    print(f"  🤖 {text_model()} 抽取深读细节…")
    raw = chat_complete(
        system=DEEP_READ_SYSTEM,
        user=user_msg,
        max_tokens=8000,
        response_format_json=True,
    )
    details = _normalize_deep_read_details(extract_json(raw), article)
    required = (
        "outline", "all_numbers", "all_quotes", "people",
        "key_terms", "concrete_scenes", "actual_opening",
        "actual_ending", "narrative_beats", "author_stance",
    )
    missing = [k for k in required if k not in details]
    if missing:
        raise RuntimeError(f"深读结果缺字段且兜底补齐失败: {missing}")
    return details, agent_id


# ============================================================
# 指定话题模式：没有合适文章时，让模型用自身知识写科普细节
# ============================================================
SELF_AUTHOR_SYSTEM = """你是「AI财知道」的资深科普研究员。用户会给你一个话题，请基于你已掌握的可靠知识，整理出适合改编成中文短视频的素材。

要求：
- 面向完全不懂的小白，用通俗易懂、科普向的方式组织内容。
- 只写你**有把握、确定为真**的事实；不确定的数字/日期宁可不写，绝不编造具体数字、引语或人名。
- 全部用中文输出。只输出严格 JSON。"""

SELF_AUTHOR_USER = """【话题】{title_hint}

{provided_block}【当前真实日期】{today}

请基于你掌握的可靠知识，输出一个 JSON 对象（不要 markdown，不要解释），键名严格如下：
- `outline`：这个话题讲清楚需要的段落 outline，每段一行中文概括（≤30 字），至少 6 行，按由浅入深的科普顺序。
- `all_numbers`：与话题相关、你**有把握**的关键数字/比例/时间，附上下文；不确定就少写或留空数组。
- `all_quotes`：相关的经典说法/定义/观点（可空数组），每条注明出处或属于常识。
- `people`：相关的关键人物及身份（可空）。
- `companies_or_institutions`：相关公司/机构（可空）。
- `key_terms`：话题涉及的术语，每个一句中文白话解释（≤25 字）。
- `concrete_scenes`：能帮助理解的具体例子/场景 3-6 条，越具体越好。
- `everyday_analogies`：针对话题里最难懂的概念，提出 3-6 个**生活化类比或例子**（用买菜、点外卖、租房、打车、开奶茶店、追剧等普通人熟悉的事打比方），格式「X 就好比 Y」，帮零基础观众秒懂。
- `actual_opening`：一个能抓住普通观众的开场（≤80 字）。
- `actual_ending`：一个收尾/启示句（≤120 字）。
- `narrative_beats`：科普讲解的节奏 5-8 拍（「先…然后…最后…」）。
- `author_stance`：本栏目对这个话题的一句话判断/态度。
- `omit_in_video`：建议不展开讲的内容 2-4 条。
"""


def author_details_from_knowledge(
    title_hint: str,
    *,
    provided_content: str | None = None,
    reference_only: bool = False,
) -> dict:
    """没有合适文章时，让 Opus 基于自身知识产出科普向 details。

    reference_only=True 时，provided_content 仅作"背景参考材料"：当材料与你
    所知的最新情况冲突时，以最新、最准确的数字/事实为准（用于"指定话题但没有
    足够及时的单篇文章、需综合材料 + 最新认知自写"的场景）。
    """
    provided_block = ""
    if provided_content and provided_content.strip():
        if reference_only:
            provided_block = (
                "【参考材料（多篇近期报道的摘要，可能时间不一/数字偏旧）】\n"
                f"{provided_content.strip()}\n\n"
                "使用要求：把上述材料当作背景参考，提炼其中的事实脉络；但当材料里的"
                "数字、市值、排名等与你所掌握的最新、最准确情况不一致时，以最新情况为准，"
                "并优先采用最新数据，不要被旧数字带偏。\n\n"
            )
        else:
            provided_block = (
                "【用户已提供的内容（请作为主要依据，可在此基础上补充常识，但不要与之矛盾）】\n"
                f"{provided_content.strip()}\n\n"
            )
    user_msg = SELF_AUTHOR_USER.format(
        title_hint=title_hint,
        provided_block=provided_block,
        today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )
    print(f"  🤖 {text_model()} 用自身知识整理「{title_hint}」科普细节…")
    raw = chat_complete(
        system=SELF_AUTHOR_SYSTEM,
        user=user_msg,
        max_tokens=6000,
        response_format_json=True,
    )
    article_stub = {
        "title": title_hint,
        "summary_zh": title_hint,
        "thesis": title_hint,
        "key_facts": [title_hint],
        "narrative_arc": "科普讲解",
    }
    return _normalize_deep_read_details(extract_json(raw), article_stub)


# ============================================================
# 阶段二：基于文章改编脚本
# ============================================================
def max_slides() -> int:
    """单条视频最多正文页数。默认 4（再加 1 张全屏封面海报 = 总共 5 张图）。
    可用 AIVIDEO_MAX_SLIDES 覆盖。"""
    try:
        return max(3, int(os.environ.get("AIVIDEO_MAX_SLIDES", "4")))
    except ValueError:
        return 4


ADAPT_SCRIPT_PROMPT = """你是抖音栏目「AI财知道 · 每天一个 AI 财经为什么」的短视频编剧。

任务：把用户给出的文章细节改成 3-4 页中文短视频问答脚本（页数宁少勿多，最多 4 页，节奏快）。只讲文章里有依据的事实，不虚构。

**核心目标：降低观看门槛、提升完播率。** 观众大多是没什么基础的普通财经学习者，原文又往往是专业文章。你的工作不是把文章观点念一遍，而是当一个会讲故事的老师，把专业内容**翻译成大白话**，让一个完全不懂的人也能一听就懂、愿意看到最后。

输出必须是单个 JSON 对象，且只需要这些字段：
{
  "title": "6-18字中文问句标题",
  "keyword": "2-8字关键词",
  "cold_open": "12-28字冷开场：先生活场景再反差，禁止纯术语",
  "cold_open_type": "conflict|number|question|myth_bust",
  "theme_cluster": "optical_module|ai_chip|ev_auto|macro_rates|consumer_platform|general",
  "angle": "10-24字本篇唯一角度",
  "hashtags": ["3-5个能蹭上的热点大词，优先用大家真会搜的赛事/事件/公司名，别自创窄词或写品牌名"],
  "slides": [
    {
      "headline": "6-14字上屏标题",
      "narration": "口播：第1页40-120字，其余页50-180字",
      "image_prompt": "English diagram prompt",
      "on_image_text": ["中文标签1", "中文标签2", "中文标签3"]
    }
  ]
}

【通俗生动·硬性要求，违反就是失败】：
- **大白话优先**：能用日常说法就别用专业术语。一旦出现普通人不懂的概念（如市盈率、毛利率、算力、流动性、估值、护城河、降息等），必须**当场用一句生活化的比喻或熟悉的例子**讲清它是什么，再往下说。例：与其说「毛利率下滑」，不如说「卖一杯奶茶以前能赚 4 块，现在只能赚 2 块」。
- **多打比方、多举例**：尽量把抽象数字和逻辑落到具体场景上——用买菜、点外卖、租房、打车、开奶茶店、追剧这类大家熟悉的事来类比公司经营、行情、技术原理。能举一个生活化例子说明的，就不要干巴巴讲道理。
- **少念观点、多讲故事**：不要把文章里的判断和结论直接搬运过来念（「文章认为/数据显示……」式的复述）。要消化成自己的话，用「打个比方」「你想象一下」「这就好比」「说人话就是」这种口吻把道理讲活。
- **冷开场 cold_open（硬性，单独字段）**：12-28 字、一句话说完，**仅作口播+底部字幕**（合成时不会印在封面图上）。**必须让零基础路人 3 秒听懂「跟我有啥关系」**——先用手机/涨价/买菜/工资/家电等生活场景做入口，再抛数字/反问/反常识；禁止「今天讲…」和纯术语开场。封面 slides[0].narration **不要重复 cold_open**，从「说人话就是」由浅入深。
- **节奏轻快、有人味**：像跟朋友唠嗑，可以适度用口语化的小调侃、反问、感叹，但不浮夸、不标题党、不虚构。宁可信息密度低一点也要讲明白，别堆砌。
- 注意：以上「生动口语」要求不能突破后面的合规红线（不荐股、不喊单、不出现股票代码等）。

规则：
- hashtags：写 3-5 个**这条视频能蹭上、且大家真的会去搜的「热点大词」**，目的是借现成的流量入口被刷到。挑选优先级：①本条强相关的**当下热点事件/赛事/节日/热门话题大词**（如 世界杯、欧冠、NBA总决赛、双十一、英伟达财报），这类词搜索量大、有现成话题广场，优先放前面；②核心主角（公司/产品/个股名）；③所属市场（A股 / 美股 / 港股 / 中概股，按真实归属写，A股 个股就写 A股，别乱加美股）；④所属板块或概念（如 半导体、算力、电力、机器人、体育经济）。
  - **宁可用大家都在搜的大词，也不要自己造没人搜的窄词**（如「赛事生意」「体育超级月」这种自创短语就别用，换成「世界杯」「NBA总决赛」这种通用热词）。
  - **不要写品牌频道名（如 AI财知道）**当标签——新号自创话题没人搜，纯属浪费坑位，发布程序也不会再补品牌标签。
  - 每个一般 2-8 字（英文公司名/赛事名可稍长），不带 # 号，宁少勿多、不要凑无关泛词。例：讲 A股 电力股涨停写 ["A股","电力股","涨停"]；讲英伟达财报写 ["英伟达","美股","财报"]；讲三大体育决赛扎堆写 ["世界杯","欧冠","NBA总决赛","体育经济"]。
- slides 3-4 页（最多 4 页）；第 1 页是封面正文页（非冷开场），封面 narration 必须 40-120 字；其余页 narration 50-180 字；最后一页是结论/影响/警示。
- 最后一页的 narration 收尾时，要**先根据这个话题自然抛出一个开放式问题**引导观众去评论区讨论（结合本期具体内容，不要套「你怎么看」这种空话，要有具体钩子），**再**引导互动：**必须明确提到「收藏」**（财经类收藏权重高），例如「觉得有用就收藏下来，对照看盘用」；可顺带提关注，但**不要只喊点赞**；不要生硬。
- title 必须是问句，优先使用「什么是 X？」「X 为什么火了？」「X 到底意味着什么？」「X 财报到底好不好？」「X 为什么大涨/大跌？」这类搜索友好标题。
- 不要输出 source、article、layout、lead_in、chapter_title、concept；这些由程序自动补。
- narration 用朋友聊天式中文，避免新闻腔；不要念出“AI财知道”。
- 口播要像栏目自己的财经解读：把文章、作者、机构观点只当作内部依据，不要主动说「文章认为」「作者指出」「文中提到」「某某的观点」。
- 如果需要交代不确定性，用「从这些数据看」「关键要看」「风险在于」这类表达，不要把判断外包给来源。
- on_image_text 每页 3-8 条，每条不超过 12 字。
- image_prompt 用英文描述白板手绘图内容，不要写风格词。
- 【合规红线，必须遵守，否则账号会被封禁】：
  - 严禁出现任何股票代码（如 600519、000001、00700、00700.HK、NVDA、09988 这类 A股6位 / 港股5位 / 美股字母代码），口播、标题、上屏文字、hashtags 里都不许带代码，只说公司或板块名字。
  - 严禁荐股、喊单、带单、给目标价/买卖点/买入卖出评级/仓位建议，禁用「买入」「卖出」「满仓」「抄底」「加仓」「目标价」「稳赚」「包赚」「必涨」「必跌」「翻倍」「收益率」「跟我买」「带你赚」「内幕」「内部消息」「稳赢」这类字眼。
  - 只做客观信息梳理和原理解释，可以分析逻辑与风险，但不要给出可执行的操作指令。
  - on_image_text、hashtags 同样不许出现股票代码或荐股词。
- 只输出 JSON，不要 markdown，不要解释。
"""


# ============================================================
# 校验：宽松版（页数 3-10、layout 只分 cover/body）
# ============================================================
def _trim_to(s: str, max_chars: int) -> str:
    """长度超界时温和裁剪（保留常用标点尾），用于软修复 lead_in / chapter_title 等。"""
    s = (s or "").strip()
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    # 优先在末尾的中文标点处切一刀，避免半截词
    for tail in ("，", "。", "、", "：", "；", "—"):
        if tail in cut:
            cut = cut.rsplit(tail, 1)[0]
            if len(cut) >= max_chars // 2:
                break
    return cut.rstrip("，。、：；—,.")


def _trim_narration_to(s: str, max_chars: int, *, min_chars: int) -> str:
    """按句子收口口播，避免模型轻微超长导致整篇脚本失败。"""
    s = re.sub(r"\s+", "", (s or "").strip())
    if len(s) <= max_chars:
        return s

    sentences = [x for x in re.findall(r"[^。！？!?；;]+[。！？!?；;]?", s) if x.strip()]
    out = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(out) + len(sent) > max_chars:
            break
        out += sent
    if len(out) >= min_chars:
        return out.rstrip("，、；;：:") or s[:max_chars].rstrip("，、；;：:")

    cut = s[:max_chars]
    for tail in ("。", "！", "？", "；", "，", "、"):
        if tail in cut:
            candidate = cut.rsplit(tail, 1)[0].strip()
            if len(candidate) >= min_chars:
                return candidate.rstrip("，、；;：:")
    return cut.rstrip("，、；;：:")


def _compact_title(s: str, max_chars: int = 24) -> str:
    """标题尽量别把英文品牌切半；先去副标题，再必要时按词边界裁剪。"""
    s = (s or "").strip()
    for sep in ("：", "，", "！", "？", " - ", "｜", "|"):
        if sep in s and len(s) > max_chars:
            left = s.split(sep, 1)[0].strip()
            if 6 <= len(left) <= max_chars:
                return left
    if len(s) <= max_chars:
        return s
    cut = s[:max_chars]
    if re.search(r"[A-Za-z]$", cut) and max_chars < len(s) and re.match(r"[A-Za-z]", s[max_chars]):
        cut = re.sub(r"[A-Za-z]+$", "", cut).rstrip()
    return cut.rstrip("，。、：；—,.") or s[:max_chars]


def _chapter_from_headline(headline: str, fallback: str) -> str:
    """章节名偏中文，避免 ChatGPT/OpenAI 这类英文词被硬截断。"""
    headline = (headline or "").strip()
    chunks = re.findall(r"[\u4e00-\u9fff]{2,8}", headline)
    if chunks:
        return _trim_to(chunks[0], 6)
    return _trim_to(fallback, 6)


def soft_sanitize_script(data: dict) -> dict:
    """把模型的简单 JSON 规范化为合成管线需要的完整 schema。"""
    if not isinstance(data, dict):
        return data
    title = _strip_stock_codes(str(data.get("title") or "").strip())
    if title:
        data["title"] = _compact_title(title)
    cold_open = _strip_stock_codes(str(data.get("cold_open") or "").strip())
    if cold_open:
        data["cold_open"] = _trim_to(cold_open, 28)
    data["angle"] = _trim_to(_strip_stock_codes(str(data.get("angle") or "").strip()), 24)
    tc = str(data.get("theme_cluster") or "").strip()
    if not tc:
        try:
            from theme_clusters import infer_theme_cluster

            tc = infer_theme_cluster(
                str(data.get("title") or ""),
                str(data.get("cold_open") or ""),
                str(data.get("angle") or ""),
            )
        except Exception:  # noqa: BLE001
            tc = "general"
    data["theme_cluster"] = tc or "general"
    plan = data.get("_topic_plan")
    if isinstance(plan, dict):
        if not data.get("cold_open") and plan.get("cold_open"):
            data["cold_open"] = _trim_to(_strip_stock_codes(str(plan["cold_open"])), 28)
        if not data.get("angle") and plan.get("angle"):
            data["angle"] = _trim_to(str(plan["angle"]), 24)
        if data.get("theme_cluster") in ("", "general") and plan.get("theme_cluster"):
            data["theme_cluster"] = str(plan["theme_cluster"])
    # 规范化 hashtags：去 # / 去空 / 去重 / 去股票代码 / 每个 ≤8 字 / 最多 5 个
    raw_tags = data.get("hashtags")
    if isinstance(raw_tags, list):
        clean_tags: list[str] = []
        for t in raw_tags:
            t = re.sub(r"^#+", "", str(t or "")).strip(" ，。、：；#,.!?！？")
            t = _strip_stock_codes(t)
            if t and len(t) <= 14 and t not in clean_tags:
                clean_tags.append(t)
        data["hashtags"] = clean_tags[:5]
    else:
        data["hashtags"] = []
    slides = data.get("slides")
    if not isinstance(slides, list):
        return data
    if not data.get("cold_open") and slides and isinstance(slides[0], dict):
        first = str(slides[0].get("narration") or "").strip()
        sent = re.split(r"[。！？!?]", first, maxsplit=1)[0].strip()
        if 12 <= len(sent) <= 28:
            data["cold_open"] = sent
    # 控制页数：超出上限时保留前 N-1 页 + 最后一页（结论），避免砍掉收尾
    limit = max_slides()
    if len(slides) > limit:
        slides = slides[: limit - 1] + [slides[-1]]
        data["slides"] = slides
    for i, slide in enumerate(slides):
        if not isinstance(slide, dict):
            continue
        slide["layout"] = "cover" if i == 0 else "body"
        for _f in ("headline", "narration", "subtitle", "lead_in", "concept"):
            if isinstance(slide.get(_f), str):
                slide[_f] = _strip_stock_codes(slide[_f])
        if isinstance(slide.get("narration"), str):
            slide["narration"] = _trim_narration_to(
                slide["narration"],
                120 if i == 0 else 220,
                min_chars=40 if i == 0 else 50,
            )
        headline = str(slide.get("headline") or f"第{i + 1}页").strip()
        slide["headline"] = _trim_to(headline, 14)
        if not str(slide.get("chapter_title") or "").strip():
            slide["chapter_title"] = _chapter_from_headline(headline, "开场" if i == 0 else "拆解")
        if not str(slide.get("concept") or "").strip():
            slide["concept"] = _trim_to(str(slide.get("narration") or headline), 25)
        if not str(slide.get("image_prompt") or "").strip():
            slide["image_prompt"] = f"diagram explaining: {headline}"
        labels = slide.get("on_image_text")
        if not isinstance(labels, list):
            labels = []
        labels = [_trim_to(_strip_stock_codes(str(x)), 12) for x in labels if _strip_stock_codes(str(x)).strip()]
        while len(labels) < 3:
            labels.append(_trim_to(headline, 12) or "AI热点")
        slide["on_image_text"] = labels[:8]
        if "chapter_title" in slide:
            slide["chapter_title"] = _chapter_from_headline(str(slide.get("chapter_title") or ""), "开场" if i == 0 else "拆解")
        if i == 0:
            sub = str(slide.get("subtitle") or slide.get("headline") or title or "前沿热点").strip()
            slide["subtitle"] = _trim_to(sub, 24)
        else:
            lead = str(slide.get("lead_in") or slide.get("headline") or "接着看").strip()
            slide["lead_in"] = _trim_to(lead, 14)
    _ensure_save_cta_on_last_slide(slides)
    return data


def _ensure_save_cta_on_last_slide(slides: list) -> None:
    """最后一页口播缺「收藏」时补一句财经向收藏引导（不破坏字数上限）。"""
    if not slides:
        return
    last = slides[-1]
    if not isinstance(last, dict):
        return
    n = str(last.get("narration") or "").strip()
    if not n or "收藏" in n:
        return
    suffix = _SAVE_CTA_SUFFIX
    max_len = 220
    if len(n) + 1 + len(suffix) <= max_len:
        last["narration"] = n.rstrip("。！？,.!?") + "。" + suffix
        return
    # 超长时替换末尾常见的「点赞/关注」套话
    for old in (
        "点个关注加点赞，下条更新别错过！",
        "点个关注，明天同一时间见！",
        "点赞关注，",
        "点个赞，",
    ):
        if old in n:
            n = n.replace(old, "收藏下来对照看盘用。")
            if "收藏" in n and len(n) <= max_len:
                last["narration"] = n
                return
    trimmed = _trim_to(n, max_len - len(suffix) - 1)
    last["narration"] = trimmed.rstrip("。！？,.!?") + "。" + suffix


def _script_all_publish_texts(script: dict) -> list[tuple[str, str]]:
    """返回 (字段说明, 文本) 供合规扫描。"""
    out: list[tuple[str, str]] = [
        ("title", str(script.get("title") or "")),
        ("cold_open", str(script.get("cold_open") or "")),
    ]
    for tag in script.get("hashtags") or []:
        out.append(("hashtag", str(tag)))
    for i, slide in enumerate(script.get("slides") or [], start=1):
        if not isinstance(slide, dict):
            continue
        for key in ("headline", "subtitle", "narration", "lead_in"):
            out.append((f"第{i}页.{key}", str(slide.get(key) or "")))
        for j, item in enumerate(slide.get("on_image_text") or []):
            out.append((f"第{i}页.on_image_text[{j}]", str(item)))
    return out


def douyin_pre_publish_scan(script: dict) -> tuple[list[str], list[str]]:
    """发布前预审：返回 (warnings, blocking_errors)。blocking 应中止发布。"""
    warnings: list[str] = []
    errors: list[str] = []
    slides = script.get("slides") or []
    cold_open = str(script.get("cold_open") or "").strip()
    if cold_open:
        if _COVER_WEAK_HOOK.match(cold_open):
            warnings.append(f"冷开场偏平（「{cold_open}」），建议生活场景+反差")
        elif not _COLD_OPEN_LIFE.search(cold_open):
            warnings.append(f"冷开场缺生活入口（「{cold_open}」），路人可能听不懂")
        elif not re.search(r"[\d%％？?！!]|为什么|怎么|难道|居然|其实|别|错|不是", cold_open):
            warnings.append(f"冷开场可再加数字/反问（「{cold_open}」）")
    elif slides and isinstance(slides[0], dict):
        warnings.append("缺少 cold_open 冷开场字段，成片将退回旧封面逻辑")
    if slides and isinstance(slides[-1], dict):
        last_n = str(slides[-1].get("narration") or "")
        if "收藏" not in last_n:
            warnings.append("最后一页口播未提到「收藏」，建议加上「收藏下来对照看盘用」")

    for label, txt in _script_all_publish_texts(script):
        if not txt.strip():
            continue
        for p in _DOUYIN_SENSITIVE_BLOCK:
            if p in txt:
                errors.append(f"{label} 含预审拦截词「{p}」")
        for p in _DOUYIN_SENSITIVE_WARN:
            if p in txt:
                warnings.append(f"{label} 含敏感词「{p}」（易触发平台限流）")
        for p in _reco_banned_for(script=script):
            if p in txt:
                errors.append(f"{label} 含违规词「{p}」")
        for pat in _STOCK_CODE_PATTERNS:
            m = pat.search(txt)
            if m:
                errors.append(f"{label} 含股票代码「{m.group(0)}」")
    return warnings, errors


def print_douyin_pre_publish_scan(script: dict, *, strict: bool = False) -> bool:
    """打印预审结果；strict=True 且有 blocking 时返回 False。"""
    warnings, errors = douyin_pre_publish_scan(script)
    if warnings:
        print("[douyin预审] 建议优化：", file=sys.stderr)
        for w in warnings:
            print(f"  ⚠ {w}", file=sys.stderr)
    if errors:
        print("[douyin预审] 须修改后再发：", file=sys.stderr)
        for e in errors:
            print(f"  ✗ {e}", file=sys.stderr)
    if not warnings and not errors:
        print("[douyin预审] 通过（标题/口播/上屏文字）", file=sys.stderr)
    if strict and errors:
        return False
    return True


_BANNED_PHRASES = (
    "口径", "交叉验证", "被写作", "隐含地", "交表", "措辞", "援引", "信源",
    "联手", "揪出", "悄悄启动", "雪片般", "一口气挖", "引发热议", "再次刷新",
    "令人瞩目", "值得关注",
)
_FORMAL_ATTRIBUTION = re.compile(
    r"文章认为|文章指出|文章提到|文章称|文中认为|文中指出|文中提到|"
    r"作者认为|作者指出|作者提到|作者称|"
    r"报道指|报道称|报道提到|报道认为|文章援引|消息人士|"
    r".{1,12}的观点|观点认为"
)
_COVER_BAD_START = re.compile(r"^(文章|报道|消息|援引|作者|文中|据.{1,6}报道)")

# ============================================================
# 合规：禁止股票代码 + 荐股/喊单类违规表达（容易被平台封禁）
# ============================================================
# 股票代码：A股 6 位（6/0/3/4/8 开头）、港股 4-5 位、美股放在括号/点号里的字母代码。
_STOCK_CODE_PATTERNS = (
    # A股 6 位数字（前后不接其它数字，避免误伤年份/金额）
    re.compile(r"(?<![\d.])(?:6\d{5}|0\d{5}|3\d{5}|4\d{5}|8\d{5})(?![\d.])"),
    # 港股代码：纯数字 4-5 位且带 .HK，或「港股 00700」这类
    re.compile(r"(?<![\d.])\d{4,5}\.HK\b", re.IGNORECASE),
    re.compile(r"(?<![\d.])0\d{3,4}(?![\d.])"),
    # 美股 ticker：括号里的全大写字母（如 (NVDA)、（AAPL））
    re.compile(r"[（(][A-Z]{1,5}[)）]"),
    # 交易所前缀写法：NASDAQ: NVDA / 纽交所:BABA / SH600519 / SZ000001 / HK00700
    re.compile(r"\b(?:NASDAQ|NYSE|SSE|SZSE|HKEX|SH|SZ|HK)[:：]?\s*[A-Z0-9]{2,6}\b", re.IGNORECASE),
)

# 荐股/喊单/收益承诺类违规词（口播、标题、上屏文字都不许出现）
_RECO_BANNED_STRICT = (
    "荐股", "喊单", "带单", "跟我买", "带你赚", "目标价", "买入评级", "卖出评级",
    "满仓", "加仓", "减仓", "抄底", "梭哈", "全仓", "买点", "卖点", "买入信号",
    "稳赚", "包赚", "稳赢", "必涨", "必跌", "翻倍", "收益率", "内幕消息", "内部消息",
    "买入", "卖出", "保证收益", "稳赚不赔", "躺赚", "月入", "免费荐股", "涨停板预测",
)
# Cursor 新流水线：允许客观叙述里的「买入」「卖出」（如资金卖出、抛售），仍禁喊单荐股短语
_RECO_BANNED_RELAXED = tuple(
    x for x in _RECO_BANNED_STRICT if x not in ("买入", "卖出")
)
_RECO_BANNED = _RECO_BANNED_STRICT  # 兼容旧引用


def compliance_relaxed(*, article: dict | None = None, script: dict | None = None) -> bool:
    """新流水线（make-and-publish-new）用宽松合规：不单禁「买入/卖出」二字。"""
    flag = os.environ.get("AIVIDEO_COMPLIANCE_RELAXED", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return True
    if os.environ.get("AIVIDEO_SOURCE", "").strip().lower() == "cursor":
        return True
    for obj in (article, script):
        if isinstance(obj, dict) and obj.get("_compliance_relaxed"):
            return True
        art = (obj or {}).get("article") if isinstance(obj, dict) else None
        if isinstance(art, dict) and art.get("_compliance_relaxed"):
            return True
    return False


def _reco_banned_for(*, article: dict | None = None, script: dict | None = None) -> tuple[str, ...]:
    if compliance_relaxed(article=article, script=script):
        return _RECO_BANNED_RELAXED
    return _RECO_BANNED_STRICT

# 抖音预审/灵犬常见敏感表达（发布前扫描，命中则警告或拦截）
_DOUYIN_SENSITIVE_WARN = (
    "私信加我", "加微信", "加V", "扫码", "二维码", "进群", "领福利",
    "保本", "零风险", "高收益", "财富自由", "一夜暴富", "跟着买", "跟着赚",
)
_DOUYIN_SENSITIVE_BLOCK = (
    "保证收益", "稳赚不赔", "免费荐股", "涨停板预测", "内幕", "带单",
)

_COVER_WEAK_HOOK = re.compile(
    r"^(今天|咱们|我们|接下来|首先|这一期|这期|大家好|本期|来聊|来说说|讲一下|说说)"
)
# 冷开场须带生活化入口（路人 3 秒能建立关联）
_COLD_OPEN_LIFE = re.compile(
    r"你|大家|普通人|手机|电脑|家电|奶茶|外卖|买菜|超市|房租|工资|涨价|便宜了|贵了|"
    r"没想到|其实|就像|好比|家里|日常|生活|用电|充电|追剧|刷视频|工资条|账单"
)
_COLD_OPEN_JARGON_ONLY = re.compile(
    r"(?i)MLCC|CPO|HBM|GPU|EPS|PE\b|硅光|800G|1\.6T|换手率|市盈率|概念股|涨停潮|"
    r"光模块|供给瓶颈|产业链|标的|估值|财报|IPO|龙虎榜"
)
_SAVE_CTA_SUFFIX = "觉得有用就收藏下来，对照看盘用。"


def _strip_stock_codes(text: str) -> str:
    """从文本里去掉股票代码（合规软修复）。括号内代码连括号一起删。"""
    if not text:
        return text
    for pat in _STOCK_CODE_PATTERNS:
        text = pat.sub("", text)
    # 清掉去掉代码后残留的空括号 / 多余分隔符
    text = re.sub(r"[（(]\s*[)）]", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ，、:：")


def _slide_texts(slide: dict) -> list[str]:
    return [
        str(slide.get("headline") or ""),
        str(slide.get("subtitle") or ""),
        str(slide.get("narration") or ""),
    ]


def validate_article_script(data: dict, article: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("根节点必须是 object")
    for key in ("title", "keyword", "slides", "source"):
        if key not in data:
            raise ValueError(f"缺少 {key}")

    cold_open = str(data.get("cold_open") or "").strip()
    if not cold_open:
        raise ValueError("缺少 cold_open（12-28 字冷开场，一句话说完）")
    if not (12 <= len(cold_open) <= 28):
        raise ValueError(f"cold_open 须 12-28 字，当前 {len(cold_open)}: {cold_open!r}")
    if _COVER_WEAK_HOOK.match(cold_open):
        raise ValueError(f"cold_open 禁止平铺开头: {cold_open!r}")
    if not _COLD_OPEN_LIFE.search(cold_open):
        raise ValueError(
            f"cold_open 必须含生活化入口（你/手机/涨价/买菜等），让路人 3 秒听懂: {cold_open!r}"
        )
    if _COLD_OPEN_JARGON_ONLY.search(cold_open) and not re.search(
        r"手机|电脑|家电|买菜|奶茶|工资|账单|涨价|贵了|便宜", cold_open
    ):
        raise ValueError(
            f"cold_open 勿以纯财经术语开场，请改成生活场景+反差: {cold_open!r}"
        )
    for p in _reco_banned_for(article=article):
        if p in cold_open:
            raise ValueError(f"cold_open 含违规词「{p}」")
    for pat in _STOCK_CODE_PATTERNS:
        m = pat.search(cold_open)
        if m:
            raise ValueError(f"cold_open 含股票代码「{m.group(0)}」")

    title = str(data["title"]).strip()
    if len(title) < 4:
        raise ValueError(f"title 过短，当前 {len(title)}: {title!r}")
    if len(title) > 30:
        data["title"] = title[:30].rstrip("，。！？,.!? ")
    for p in _reco_banned_for(article=article):
        if p in title:
            raise ValueError(f"title 含荐股/违规词「{p}」（合规红线）")
    for pat in _STOCK_CODE_PATTERNS:
        m = pat.search(title)
        if m:
            raise ValueError(f"title 含股票代码「{m.group(0)}」（合规红线）")

    src = data.get("source") or {}
    src_url = str(src.get("url") or "")
    # 指定话题模式（自带内容/模型自写）允许没有来源 URL；有 URL 时必须合法。
    if src_url and not src_url.startswith("http"):
        raise ValueError("source.url 必须是有效链接")
    if not src_url and not article.get("_no_source"):
        raise ValueError("source.url 必须是有效链接")

    slides = data["slides"]
    limit = max_slides()
    if not isinstance(slides, list) or not (3 <= len(slides) <= limit):
        raise ValueError(f"slides 数量须 3-{limit}，当前 {len(slides) if isinstance(slides, list) else '非数组'}")

    formal_count = 0
    for i, slide in enumerate(slides):
        page = i + 1
        layout = slide.get("layout") or ("cover" if i == 0 else "body")
        slide["layout"] = layout
        if i == 0 and layout != "cover":
            raise ValueError("第 1 页 layout 必须为 cover")
        if i > 0 and layout == "cover":
            raise ValueError(f"第 {page} 页不应为 cover")

        for key in ("headline", "narration", "image_prompt", "chapter_title", "concept"):
            if not str(slide.get(key) or "").strip():
                raise ValueError(f"第 {page} 页缺少 {key}")

        ch = str(slide["chapter_title"]).strip()
        if not (2 <= len(ch) <= 6):
            raise ValueError(f"第 {page} 页 chapter_title 须 2-6 字: {ch!r}")

        if layout == "cover":
            if not str(slide.get("subtitle") or "").strip():
                raise ValueError("cover 页缺少 subtitle")
            sub = str(slide["subtitle"]).strip()
            if not (6 <= len(sub) <= 24):
                raise ValueError(f"cover subtitle 须 6-24 字，当前 {len(sub)}")
            if _COVER_BAD_START.match(str(slide["narration"]).strip()):
                raise ValueError("cover narration 禁止以「文章/报道/消息/据...」开头")
        else:
            lead_in = str(slide.get("lead_in") or "").strip()
            if not lead_in:
                raise ValueError(f"第 {page} 页缺少 lead_in（≤14 字衔接锚点）")
            if len(lead_in) > 14:
                raise ValueError(f"第 {page} 页 lead_in ≤14 字，当前 {len(lead_in)}")

        n = str(slide["narration"]).strip()
        nlen = len(n)
        if layout == "cover":
            if not (40 <= nlen <= 120):
                raise ValueError(f"cover narration 须 40-120 字，当前 {nlen}")
        else:
            if not (50 <= nlen <= 220):
                raise ValueError(f"第 {page} 页 narration 须 50-220 字，当前 {nlen}")

        oit = slide.get("on_image_text") or []
        if not isinstance(oit, list) or not (3 <= len(oit) <= 12):
            raise ValueError(f"第 {page} 页 on_image_text 须 3-12 条")
        for j, item in enumerate(oit):
            if not isinstance(item, str) or not item.strip() or len(item) > 16:
                raise ValueError(f"第 {page} 页 on_image_text[{j}] 须 1-16 字非空: {item!r}")

        check_texts = _slide_texts(slide) + [str(x) for x in (slide.get("on_image_text") or [])]
        for txt in check_texts:
            for p in _BANNED_PHRASES:
                if p in txt:
                    raise ValueError(f"第 {page} 页含禁用词「{p}」")
            for p in _reco_banned_for(article=article):
                if p in txt:
                    raise ValueError(f"第 {page} 页含荐股/违规词「{p}」（合规红线，不许出现）")
            for pat in _STOCK_CODE_PATTERNS:
                m = pat.search(txt)
                if m:
                    raise ValueError(f"第 {page} 页含股票代码「{m.group(0)}」（合规红线，只说公司/板块名）")
        for txt in _slide_texts(slide):
            formal_count += len(_FORMAL_ATTRIBUTION.findall(txt))

    if formal_count > 0:
        raise ValueError(f"口播禁止显性引用文章/作者/外部观点，当前 {formal_count} 次")

    return data


def merge_article_into_script(data: dict, article: dict) -> dict:
    src = data.get("source")
    if not isinstance(src, dict):
        src = {}
    data["source"] = {
        "title": src.get("title") or article.get("title") or "",
        "url": src.get("url") or article.get("url") or "",
        "site": src.get("site") or article.get("site") or "",
    }
    if not str(data.get("keyword") or "").strip():
        data["keyword"] = (article.get("summary_zh") or "")[:6] or "AI"
    data["article"] = article
    plan = article.get("_topic_plan")
    if isinstance(plan, dict):
        data["_topic_plan"] = plan
    return data


ADAPT_FIX_PROMPT = """你上一轮输出的 JSON 脚本未通过校验。请重新输出**完整脚本 JSON**（不要 markdown，不要解释）。

校验错误：
{errors}

仍按之前要求：
- slides 长度 3-4（最多 4 页）；第 1 页 layout=cover（含 subtitle），其余 layout=body（含 lead_in）
- 每页有 chapter_title / concept / headline / narration / image_prompt / on_image_text
- 必须忠实于已选定文章原文（URL: {url}），不虚构事实
- 口播必须像「AI财知道」自己的财经解读，不要说「文章认为」「作者指出」「文中提到」「某某的观点」；来源只作内部依据。
- cold_open 必须生活化入口+反差，禁止纯术语；须输出 theme_cluster + angle；封面 narration 勿重复 cold_open 且控制在 40-120 字；最后一页引导「收藏」。
- 【合规红线】：标题/口播/上屏文字/hashtags 都严禁出现任何股票代码（A股6位、港股带.HK、美股字母代码等），也严禁荐股、喊单、目标价、买卖点、仓位建议、「稳赚/必涨/翻倍/收益率/内幕/买入/卖出」等字眼，只做客观信息梳理与原理解释。
"""


def _build_adapt_user_message(article: dict, details: dict) -> str:
    meta_block = (
        f"【已选定文章 metadata】\n"
        f"- 标题: {article.get('title', '')}\n"
        f"- 建议问句标题: {article.get('question_title', '')}\n"
        f"- 选题分数: {article.get('topic_score', '')}  优先级: {article.get('priority', '')}  类别: {article.get('category', '')}\n"
        f"- 打分理由: {article.get('score_reason', '')}\n"
        f"- 站点: {article.get('site', '')}  作者: {article.get('author') or '-'}  日期: {article.get('published_at') or '-'}\n"
        f"- URL: {article.get('url', '')}\n"
        f"- 中文一句话: {article.get('summary_zh', '')}\n"
        f"- 核心论点: {article.get('thesis', '')}"
    )
    details_block = (
        "【原文深读细节（由 Cursor 联网读完原文后整理，全部基于原文，不准再编）】\n"
        + json.dumps(details, ensure_ascii=False, indent=2)
    )
    topic_block = _topic_plan_block(article)
    relaxed_note = ""
    if compliance_relaxed(article=article):
        relaxed_note = (
            "\n【合规说明·Cursor 新流水线】客观复盘里可以使用「买入」「卖出」等中性表述"
            "（如资金卖出、抛售、买入意愿），但不要写成荐股喊单（跟我买、目标价、必涨、买卖点建议等）。\n"
        )
    return (
        f"{topic_block}{meta_block}\n\n{details_block}\n{relaxed_note}\n"
        "请严格根据上面的「原文深读细节」改编。输出字段须含 title / keyword / cold_open / "
        "cold_open_type / theme_cluster / angle / hashtags / slides；"
        "如果 metadata 里有「建议问句标题」，优先沿用或小幅润色为最终 title；"
        "slides 每页只填 headline / narration / image_prompt / on_image_text。"
        "不要输出 source、article、layout、lead_in、chapter_title、concept。"
        "写法上要直接给出本栏目的判断和解释，禁止在口播里说「文章认为」「作者指出」「文中提到」「某某的观点」；"
        "来源信息只用于事实依据，不对观众显性提及。\n"
        "面向零基础观众：尽量用大白话，遇到专业术语就借助细节里的 key_terms 白话解释和 everyday_analogies 类比，"
        "用买菜、点外卖、开奶茶店这种生活化例子把它讲活；多打比方、少照搬观点；"
        "必须输出 cold_open / theme_cluster / angle；封面 narration 不要重复 cold_open；"
        "最后一页口播要带「收藏」引导。\n\n"
        "请输出严格 JSON 对象（不要 markdown，不要解释）。"
    )


def _resolve_fixed_video_title(article: dict) -> str:
    fixed = str(article.get("_fixed_video_title") or "").strip()
    if fixed:
        return fixed
    plan = article.get("_topic_plan")
    if isinstance(plan, dict):
        return str(plan.get("fixed_video_title") or "").strip()
    return ""


def _apply_fixed_video_title(script: dict, article: dict) -> dict:
    """大盘报盘等槽位：标题固定，不让 Opus 改成问句。"""
    fixed = _resolve_fixed_video_title(article)
    if fixed:
        script["title"] = fixed
        if not str(script.get("keyword") or "").strip():
            script["keyword"] = "A股大盘"
    return script


def _topic_plan_block(article: dict) -> str:
    plan = article.get("_topic_plan")
    if not isinstance(plan, dict):
        return ""
    parts = []
    fixed = str(plan.get("fixed_video_title") or "").strip()
    if fixed:
        parts.append(f"- 视频标题（必须一字不改）: {fixed}")
    elif plan.get("title_hint"):
        parts.append(f"- 选题问句: {plan['title_hint']}")
    if plan.get("cold_open"):
        parts.append(
            f"- 冷开场（须保留生活化入口，可微调）: {plan['cold_open']}"
        )
    if plan.get("angle"):
        parts.append(f"- 本篇角度: {plan['angle']}")
    if plan.get("theme_cluster"):
        parts.append(f"- 概念簇: {plan['theme_cluster']}")
    if not parts:
        return ""
    return "【选题已定（Hook-First，请服从）】\n" + "\n".join(parts) + "\n\n"


def adapt_article_to_script(
    article: dict,
    *,
    details: dict,
    agent_id: str | None = None,
) -> tuple[dict, str | None]:
    """用 Claude Opus 4.7（via AiHubMix）基于 details 把文章改编成脚本 JSON。

    agent_id 仅用于占位/兼容旧调用，本步骤不再使用 Cursor Cloud。
    """
    system_prompt = ADAPT_SCRIPT_PROMPT
    print(f"  📄 喂给 {text_model()} 改编（含 {len(details.get('outline') or [])} 段 outline / "
          f"{len(details.get('all_quotes') or [])} 条引语 / "
          f"{len(details.get('all_numbers') or [])} 个数字）…")

    user_msg = _build_adapt_user_message(article, details)

    max_attempts = int(os.environ.get("ADAPT_MAX_ATTEMPTS", "5"))
    max_tokens = int(os.environ.get("ADAPT_MAX_TOKENS", "12000"))
    last_err: Exception | None = None
    raw_text = ""
    last_parsed: dict | None = None  # 上一轮 parse 出来的 JSON（即使 validate 失败也保留）
    base_user_msg = user_msg
    for attempt in range(max_attempts):
        try:
            raw_text = chat_complete(
                system=system_prompt,
                user=user_msg,
                max_tokens=max_tokens,
                response_format_json=True,
            )
            try:
                raw = extract_json(raw_text, require_slides=True)
                parse_ok = True
            except ValueError:
                # JSON 都没出来 → 再宽松一档：允许任意含 slides 的 JSON
                raw = extract_json(raw_text, require_slides=False)
                if not isinstance(raw.get("slides"), list):
                    raise
                parse_ok = True
            last_parsed = raw
            data = merge_article_into_script(raw, article)
            data = soft_sanitize_script(data)  # 软修复长度类违规
            validated = validate_article_script(data, article)
            print_douyin_pre_publish_scan(validated)
            return validated, agent_id
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            if attempt >= max_attempts - 1:
                break
            print(f"  ⚠️  第 {attempt + 1}/{max_attempts} 轮未通过，让 {text_model()} 修正… ({e})", file=sys.stderr)
            fix_msg = ADAPT_FIX_PROMPT.format(errors=str(e), url=article.get("url", ""))
            if last_parsed is not None:
                # 已 parse 出 JSON → 让模型基于上一轮 JSON 做小改，命中率最高
                fix_msg += (
                    "\n\n你上一轮输出的 JSON（请基于它**只改报错涉及的字段**，其它保持原样）：\n"
                    + json.dumps(last_parsed, ensure_ascii=False, indent=2)[:16000]
                )
            elif raw_text:
                # 完全没 parse 出来 → 把原文截断喂回去 + 强调输出纯 JSON
                fix_msg += (
                    "\n\n注意：上一轮你的回复**不是有效 JSON**或被截断。这次必须输出**单个完整 JSON 对象**，"
                    "不要 markdown、不要解释、不要 ```json 代码块。"
                    f"\n\n上一轮原始输出（截断）：\n{raw_text[:8000]}"
                )
            user_msg = f"{base_user_msg}\n\n========\n【修正提示】\n{fix_msg}"
        except RuntimeError as e:
            last_err = e
            if attempt >= max_attempts - 1:
                break
            print(f"  ⚠️  网络/接口失败，第 {attempt + 1}/{max_attempts} 轮重试… ({e})", file=sys.stderr)
            time.sleep(min(20, 5 * (attempt + 1)))
    raise RuntimeError(f"改编脚本失败（已重试 {max_attempts} 次）: {last_err}") from last_err


# ============================================================
# 主入口
# ============================================================
def run_article_research(
    *,
    output: str | Path,
    days: int = 7,
    exclude_urls: list[str] | None = None,
    agent_id: str | None = None,
    logs_dir: Path | None = None,
    use_selection: bool = False,
    auto_pick: bool = False,
    recent_topics: list[str] | None = None,
    source: str = "feeds",
    fresh_hours: int = 24,
    preselected_article: dict | None = None,
    preselected_details: dict | None = None,
    category: str | None = None,
) -> tuple[dict, str]:
    logs_dir = logs_dir or (ROOT / "logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    if recent_topics is None:
        try:
            from batch_aivideo import history_recent_topics

            recent_topics = history_recent_topics()
        except Exception:
            recent_topics = None

    selection_path = logs_dir / "last_article.json"
    saved: dict | None = None
    if use_selection and selection_path.is_file():
        try:
            saved = json.loads(selection_path.read_text(encoding="utf-8"))
            if not _article_looks_ok(saved):
                saved = None
        except json.JSONDecodeError:
            saved = None

    candidate_pool: list[dict] = []
    decision: dict | None = None
    if preselected_article:
        article = preselected_article
        print("[1a] 使用本轮打分队列中的预选文章")
    elif saved:
        article = saved
        print("[1a] 跳过找文章，复用 logs/last_article.json")
    else:
        if source == "feeds":
            print(f"[1a] 抓取固定信息源近 {fresh_hours} 小时 AI/财经热点…")
        else:
            print(f"[1a] 搜索过去 {days} 天 AI/财经热点长文（中英文各 3 候选）…")
        candidates, agent_id = find_articles(
            days=days, exclude_urls=exclude_urls, agent_id=agent_id,
            recent_topics=recent_topics, source=source, fresh_hours=fresh_hours,
        )
        candidate_pool = list(candidates)
        (logs_dir / "last_article_candidates.json").write_text(
            json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        article, decision = pick_article(
            candidates, auto=auto_pick, recent_topics=recent_topics,
        )
        if decision:
            (logs_dir / "last_article_decision.json").write_text(
                json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    # 打分排序作为兜底候选队列：当前最高分放第一，剩下按分数补齐。
    fallback_queue: list[dict] = [article]
    if candidate_pool and decision and isinstance(decision.get("ranking"), list):
        seen_urls = {str(article.get("url") or "")}
        for idx in decision["ranking"]:
            try:
                cand = candidate_pool[int(idx) - 1]
            except (ValueError, TypeError, IndexError):
                continue
            scored_match = None
            for row in decision.get("scored") or []:
                try:
                    if int(row.get("index") or 0) == int(idx):
                        scored_match = row
                        break
                except (TypeError, ValueError):
                    continue
            if scored_match:
                cand = dict(cand)
                cand["topic_score"] = scored_match.get("topic_score")
                cand["priority"] = scored_match.get("priority")
                cand["question_title"] = scored_match.get("question_title")
                cand["score_reason"] = scored_match.get("reason")
                cand["category"] = scored_match.get("category")
            u = str(cand.get("url") or "")
            if u and u not in seen_urls:
                fallback_queue.append(cand)
                seen_urls.add(u)

    script: dict | None = None
    last_exc: Exception | None = None
    details: dict = {}
    for try_idx, art in enumerate(fallback_queue):
        if try_idx > 0:
            print(f"\n[fallback] 第 {try_idx + 1} 候选兜底：{art.get('title')}", file=sys.stderr)
        article = art
        print(f"  ✓ 选定: {article.get('title')}")
        print(f"    站点: {article.get('site')}  日期: {article.get('published_at')}")
        print(f"    URL : {article.get('url')}")
        selection_path.write_text(
            json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        details_path = logs_dir / "last_article_details.json"
        if preselected_details and try_idx == 0:
            details = preselected_details
            print("[1b] 使用指定话题模式预备好的深读细节")
        elif use_selection and saved and try_idx == 0 and details_path.is_file():
            try:
                details = json.loads(details_path.read_text(encoding="utf-8"))
                print("[1b] 复用 last_article_details.json")
            except json.JSONDecodeError:
                details = {}

        try:
            if not details:
                print("[1b] Cursor 深读原文，抽取段落/数字/引语/场景/结尾…")
                details, agent_id = deep_read_article(article, agent_id=agent_id)
                details_path.write_text(
                    json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            print(f"  ✓ outline {len(details.get('outline') or [])} 段 / "
                  f"引语 {len(details.get('all_quotes') or [])} 条 / "
                  f"数字 {len(details.get('all_numbers') or [])} 个")

            print("[1c] Opus 4.7 按文章自身节奏改编为 3-10 页中文脚本…")
            script, _ = adapt_article_to_script(article, details=details)
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            print(f"  ✗ 这篇文章流程失败：{exc}", file=sys.stderr)
            details = {}
            if try_idx + 1 >= len(fallback_queue):
                break
            print(f"  ↻ 切换到下一候选重试…", file=sys.stderr)
            continue

    if script is None:
        raise RuntimeError(f"全部候选改编失败（共 {len(fallback_queue)} 篇）: {last_exc}") from last_exc

    try:
        import categories as _categories

        cat = category or os.environ.get("AIVIDEO_CATEGORY") or None
        resolved = _categories.resolve_category(script, cat)
        if resolved:
            script["category"] = resolved
            print(f"  🏷  子栏目：{_categories.label_of(resolved)}（{resolved}）")
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️  子栏目判定失败，用默认主题：{exc}", file=sys.stderr)

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "mode": "article",
        "days": days,
        "agent_id": agent_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "article": article,
        "research_details": details,
        "script": script,
    }
    out_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (logs_dir / "cursor_agent.json").write_text(
        json.dumps({"agent_id": agent_id}, indent=2), encoding="utf-8"
    )
    return script, agent_id


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(
        description="文章驱动调研：Cursor 找/深读热点长文 → Opus 4.7 评审/改编中文问答短视频脚本"
    )
    parser.add_argument("-o", "--output", default=str(ROOT / "logs" / "last_script.json"))
    parser.add_argument("--days", type=int, default=7, help="搜索时间窗（天），默认 7")
    parser.add_argument("--source", choices=("feeds", "exa"), default=os.environ.get("AIVIDEO_SOURCE", "exa"),
                        help="候选来源：exa=Exa Search；feeds=旧固定信息源兜底")
    parser.add_argument("--fresh-hours", type=int, default=int(os.environ.get("AIVIDEO_FRESH_HOURS", "24")),
                        help="固定信息源新鲜度窗口，默认 24 小时")
    parser.add_argument("--exclude-urls", help="已制作过的 URL，逗号分隔")
    parser.add_argument("--agent-id")
    parser.add_argument("--use-selection", action="store_true",
                        help="跳过找文章，复用 logs/last_article.json")
    parser.add_argument("--auto-pick", action="store_true",
                        help="不交互，直接由 Opus 4.7 选最佳")
    args = parser.parse_args()

    exclude_urls = [u.strip() for u in (args.exclude_urls or "").split(",") if u.strip()]
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    src_desc = f"固定信息源近 {args.fresh_hours} 小时" if args.source == "feeds" else f"Exa 近 {args.days} 天"
    print(f"[research] 文章驱动 | 候选={src_desc} | 评审/深读/改编={text_model()} (effort=low)")

    try:
        script, _ = run_article_research(
            output=args.output,
            days=args.days,
            exclude_urls=exclude_urls or None,
            agent_id=args.agent_id,
            logs_dir=logs_dir,
            use_selection=args.use_selection,
            auto_pick=args.auto_pick,
            source=args.source,
            fresh_hours=args.fresh_hours,
        )
    except (ValueError, json.JSONDecodeError, RuntimeError) as e:
        import traceback
        print(f"调研失败: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
        traceback.print_exc()
        return 1

    print(f"[done] 脚本: {args.output}")
    print(f"  title={script['title']}  slides={len(script['slides'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
