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
]

EXA_QUERIES_ZH = [
    "本周 AI 最热门 深度文章 长文",
    "AI 行业 头条 深度报道 一周",
    "大模型 最新进展 解读 深度",
    "人工智能 行业观察 评论长文",
    "AGI OR 大模型 OR 智能体 中文 深度",
]

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


PICK_CANDIDATES_SYSTEM = """你是「AI财知道」选题总编。给你一批 Exa 搜回来的候选文章（含标题/URL/站点/日期/摘要/亮点片段），请按 AI 与财经圈真实热度挑出 **{n} 篇** 适合改编为短视频问答的{lang_label}长文/深度报道。

栏目定位：用大白话回答「AI 和财经类十万个为什么」。优先选择能被概括成一个搜索型问句的热点，例如「什么是 X」「X 为什么大涨」「X 财报到底好不好」「X 对普通人有什么影响」。

挑选标准（按重要性）：
1) AI、财经、美股、中概股全网真实热度（HN 高分、X 多人转、多家媒体同步报道、Reddit/Newsletter 头条、知乎/微博/即刻热门、公众号 10w+ 等）。尤其关注大型科技股/中概股财报、股价异动、宏观数据和监管事件。
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
    source: str = "feeds",
    fresh_hours: int = 24,
) -> tuple[list[dict], str | None]:
    """获取候选文章。默认固定信息源最近 24h；Exa 保留为兜底。"""
    if source == "feeds":
        excl = {u.strip() for u in (exclude_urls or []) if u.strip()}
        candidates = [
            c for c in feed_client.fetch_feed_candidates(hours=fresh_hours)
            if str(c.get("url") or "").strip() not in excl
        ]
        if not candidates:
            raise RuntimeError("固定信息源没有抓到候选")
        print(f"  ✓ 固定信息源候选：{len(candidates)} 篇（近 {fresh_hours} 小时）")
        return candidates, agent_id

    pool_en = _exa_search_pool(
        days=days, exclude_urls=exclude_urls, queries=EXA_QUERIES_EN,
    )
    pool_zh = _exa_search_pool(
        days=days, exclude_urls=exclude_urls, queries=EXA_QUERIES_ZH,
    )
    if not pool_en and not pool_zh:
        raise RuntimeError("Exa 中英文池都没搜到任何候选")

    cands_en = _pick_from_pool(
        pool_en, n=per_lang, lang_code="en", lang_label="英文",
        exclude_urls=exclude_urls, recent_topics=recent_topics,
    )
    cands_zh = _pick_from_pool(
        pool_zh, n=per_lang, lang_code="zh", lang_label="中文",
        exclude_urls=exclude_urls, recent_topics=recent_topics,
    )
    valid = cands_en + cands_zh
    if not valid:
        raise RuntimeError("Opus 返回的候选文章均不合规")
    print(f"  ✓ 候选合并：英文 {len(cands_en)} 篇 + 中文 {len(cands_zh)} 篇 = {len(valid)} 篇")
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


PICK_BEST_SYSTEM = """你是抖音栏目「AI财知道」的选题总编。从候选资讯里挑 1 篇做短视频。

栏目定位：AI 和财经类「十万个为什么」。优先选择 24 小时内的新鲜事件，能用一个搜索型问句讲清楚「这是什么、为什么重要、会影响谁」。

挑选标准：
1) 新鲜：优先 24 小时内刚发生/刚发布/刚公开的事件。
2) 重要：技术突破、模型/产品发布、监管转折、巨头战略、开发者生态、资本/商业拐点；大型美股和中概股财报、股价异动、宏观数据也优先。
3) 可讲：有具体事实、数字、人物、冲突或趋势，不选只有一句空泛公告的软文。
4) 去重：同一主题多条只留信息密度最高的一条。

输出严格 JSON。"""

PICK_BEST_USER = """【当前真实日期】{today}（请用它作为新鲜度锚点；凡事件实际时间距今超过 60 天的候选都视为旧文，无论 published_at 写得多新，都直接舍弃，不要 pick 它）

以下是候选（JSON）。请按上述标准挑出**当下 AI 或财经圈真正热度最高且是近期事件**的 1 篇。

{candidates_json}

只输出一个 JSON 对象（不要 markdown，不要解释）：
{{
  "pick_index": 1-based 整数,
  "reason": "选它的核心理由（25-50 字，必须提到具体热度证据）",
  "ranking": [候选序号按从热到冷排列],
  "rejected_reasons": {{ "2": "为何不选 25 字内", "3": "..." }}
}}"""


def auto_pick_best_article(
    candidates: list[dict],
    *,
    recent_topics: list[str] | None = None,
) -> tuple[dict, dict]:
    """用 Opus 4.7 从候选里挑最佳。返回 (selected_article, decision_meta)。"""
    cand_view = []
    for i, c in enumerate(candidates, 1):
        cand_view.append({
            "index": i,
            "title": c.get("title"),
            "site": c.get("site"),
            "summary_zh": c.get("summary_zh"),
            "thesis": c.get("thesis"),
            "key_facts": c.get("key_facts"),
            "narrative_arc": c.get("narrative_arc"),
            "heat_score": c.get("heat_score"),
            "heat_evidence": c.get("heat_evidence"),
            "published_at": c.get("published_at"),
            "url": c.get("url"),
        })
    user_msg = PICK_BEST_USER.format(
        candidates_json=json.dumps(cand_view, ensure_ascii=False, indent=2),
        today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )
    if recent_topics:
        recent_block = "\n  - ".join(recent_topics)
        user_msg += (
            "\n\n【近期已做过的主题（最近 21 天）】"
            "这些主题/事件刚刚发过视频，**即使热度再高也不能再选**——同一主角、"
            "同一事件、同一发布的不同媒体复述都算重复。请在 ranking 与 pick_index 中规避它们：\n  - "
            + recent_block
        )
    print(f"  🤖 让 {text_model()} 评审 {len(candidates)} 篇候选…")
    raw = chat_complete(
        system=PICK_BEST_SYSTEM,
        user=user_msg,
        max_tokens=800,
    )
    decision = extract_json(raw)
    idx = int(decision.get("pick_index") or 1)
    if not (1 <= idx <= len(candidates)):
        idx = 1
    rejected = decision.get("rejected_reasons") or {}
    reject_text = str(rejected.get(str(idx)) or rejected.get(idx) or "")
    if re.search(r"已做过|重复|近期已做|21天|做过", reject_text):
        for raw_idx in decision.get("ranking") or []:
            try:
                cand_idx = int(raw_idx)
            except (TypeError, ValueError):
                continue
            if not (1 <= cand_idx <= len(candidates)):
                continue
            cand_reject = str(rejected.get(str(cand_idx)) or rejected.get(cand_idx) or "")
            if re.search(r"已做过|重复|近期已做|21天|做过", cand_reject):
                continue
            print(f"  ⚠️  pick_index 指向已做/重复主题，改选 ranking 中第一个非重复候选 [{cand_idx}]", file=sys.stderr)
            idx = cand_idx
            decision["pick_index"] = cand_idx
            break
    print(f"  ✓ 评审结果：选 [{idx}] — {decision.get('reason', '')}")
    if decision.get("ranking"):
        print(f"    排名: {decision['ranking']}")
    rej = decision.get("rejected_reasons") or {}
    for k, v in rej.items():
        print(f"    舍弃 [{k}]: {v}")
    return candidates[idx - 1], decision


def pick_article(
    candidates: list[dict],
    *,
    auto: bool = False,
    recent_topics: list[str] | None = None,
) -> tuple[dict, dict | None]:
    _print_candidates(candidates)
    if auto:
        article, decision = auto_pick_best_article(candidates, recent_topics=recent_topics)
        return article, decision
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


def deep_read_article(article: dict, *, agent_id: str | None) -> tuple[dict, str | None]:
    """Exa 抓全文 → Opus 4.7 抽细节，返回细节字典。"""
    url = str(article.get("url") or "")
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
    details = extract_json(raw)
    required = (
        "outline", "all_numbers", "all_quotes", "people",
        "key_terms", "concrete_scenes", "actual_opening",
        "actual_ending", "narrative_beats", "author_stance",
    )
    missing = [k for k in required if not details.get(k)]
    if missing:
        raise RuntimeError(f"深读结果缺字段: {missing}")
    return details, agent_id


# ============================================================
# 阶段二：基于文章改编脚本
# ============================================================
ADAPT_SCRIPT_PROMPT = """你是抖音栏目「AI财知道 · 每天一个 AI 财经为什么」的短视频编剧。

任务：把用户给出的文章细节改成 3-8 页中文短视频问答脚本。只讲文章里有依据的事实，不虚构。

输出必须是单个 JSON 对象，且只需要这些字段：
{
  "title": "6-18字中文问句标题",
  "keyword": "2-8字关键词",
  "slides": [
    {
      "headline": "6-14字上屏标题",
      "narration": "50-180字口播",
      "image_prompt": "English diagram prompt",
      "on_image_text": ["中文标签1", "中文标签2", "中文标签3"]
    }
  ]
}

规则：
- slides 3-8 页；第 1 页是封面钩子，最后一页是结论/影响/警示。
- title 必须是问句，优先使用「什么是 X？」「X 为什么火了？」「X 到底意味着什么？」「X 财报到底好不好？」「X 为什么大涨/大跌？」这类搜索友好标题。
- 不要输出 source、article、layout、lead_in、chapter_title、concept；这些由程序自动补。
- narration 用朋友聊天式中文，避免新闻腔；不要念出“AI财知道”。
- on_image_text 每页 3-8 条，每条不超过 12 字。
- image_prompt 用英文描述白板手绘图内容，不要写风格词。
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
    title = str(data.get("title") or "").strip()
    if title:
        data["title"] = _compact_title(title)
    slides = data.get("slides")
    if not isinstance(slides, list):
        return data
    for i, slide in enumerate(slides):
        if not isinstance(slide, dict):
            continue
        slide["layout"] = "cover" if i == 0 else "body"
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
        labels = [_trim_to(str(x), 12) for x in labels if str(x).strip()]
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
    return data


_BANNED_PHRASES = (
    "口径", "交叉验证", "被写作", "隐含地", "交表", "措辞", "援引", "信源",
    "联手", "揪出", "悄悄启动", "雪片般", "一口气挖", "引发热议", "再次刷新",
    "令人瞩目", "值得关注",
)
_FORMAL_ATTRIBUTION = re.compile(r"文章认为|报道指|文章称|文章援引|消息人士")
_COVER_BAD_START = re.compile(r"^(文章|报道|消息|援引|据.{1,6}报道)")


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

    title = str(data["title"]).strip()
    if not (4 <= len(title) <= 18):
        raise ValueError(f"title 须 4-18 字，当前 {len(title)}: {title!r}")

    src = data.get("source") or {}
    if not src.get("url", "").startswith("http"):
        raise ValueError("source.url 必须是有效链接")

    slides = data["slides"]
    if not isinstance(slides, list) or not (3 <= len(slides) <= 10):
        raise ValueError(f"slides 数量须 3-10，当前 {len(slides) if isinstance(slides, list) else '非数组'}")

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

        for txt in _slide_texts(slide):
            for p in _BANNED_PHRASES:
                if p in txt:
                    raise ValueError(f"第 {page} 页含禁用词「{p}」")
            formal_count += len(_FORMAL_ATTRIBUTION.findall(txt))

    if formal_count > 1:
        raise ValueError(f"全片客观引述最多 1 次，当前 {formal_count} 次")

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
    return data


ADAPT_FIX_PROMPT = """你上一轮输出的 JSON 脚本未通过校验。请重新输出**完整脚本 JSON**（不要 markdown，不要解释）。

校验错误：
{errors}

仍按之前要求：
- slides 长度 3-10；第 1 页 layout=cover（含 subtitle），其余 layout=body（含 lead_in）
- 每页有 chapter_title / concept / headline / narration / image_prompt / on_image_text
- 必须忠实于已选定文章原文（URL: {url}），不虚构事实
"""


def _build_adapt_user_message(article: dict, details: dict) -> str:
    meta_block = (
        f"【已选定文章 metadata】\n"
        f"- 标题: {article.get('title', '')}\n"
        f"- 站点: {article.get('site', '')}  作者: {article.get('author') or '-'}  日期: {article.get('published_at') or '-'}\n"
        f"- URL: {article.get('url', '')}\n"
        f"- 中文一句话: {article.get('summary_zh', '')}\n"
        f"- 核心论点: {article.get('thesis', '')}"
    )
    details_block = (
        "【原文深读细节（由 Cursor 联网读完原文后整理，全部基于原文，不准再编）】\n"
        + json.dumps(details, ensure_ascii=False, indent=2)
    )
    return (
        f"{meta_block}\n\n{details_block}\n\n"
        "请严格根据上面的「原文深读细节」改编。输出字段只允许 title / keyword / slides；"
        "slides 每页只填 headline / narration / image_prompt / on_image_text。"
        "不要输出 source、article、layout、lead_in、chapter_title、concept。\n\n"
        "请输出严格 JSON 对象（不要 markdown，不要解释）。"
    )


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
            return validate_article_script(data, article), agent_id
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
    if saved:
        article = saved
        print("[1a] 跳过找文章，复用 logs/last_article.json")
    else:
        if source == "feeds":
            print(f"[1a] 抓取固定信息源近 {fresh_hours} 小时 AI 热点…")
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

    # ranking 顺序作为兜底候选队列：当前选中放第一，剩下按 Opus 排名补齐
    fallback_queue: list[dict] = [article]
    if candidate_pool and decision and isinstance(decision.get("ranking"), list):
        seen_urls = {str(article.get("url") or "")}
        for idx in decision["ranking"]:
            try:
                cand = candidate_pool[int(idx) - 1]
            except (ValueError, TypeError, IndexError):
                continue
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
        if use_selection and saved and try_idx == 0 and details_path.is_file():
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

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "mode": "article",
        "days": days,
        "agent_id": agent_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "article": article,
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
    parser.add_argument("--source", choices=("feeds", "exa"), default=os.environ.get("AIVIDEO_SOURCE", "feeds"),
                        help="候选来源：feeds=固定信息源近 24h；exa=全网搜索兜底")
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
