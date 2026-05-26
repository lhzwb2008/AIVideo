#!/usr/bin/env python3
"""文章驱动的调研流水线（项目唯一管线）：

1. Exa AI 搜索过去 N 天 AI 圈热门英文长文 → Claude Opus 4.7 (low) 筛 3 篇候选
2. Claude Opus 4.7 评审挑 1 篇
3. Exa /contents 取该文全文 → Claude Opus 4.7 抽出段落 outline / 数字 / 引语 / 场景 / 真实结尾
4. Claude Opus 4.7 基于深读细节改编为 3-10 页中文短视频脚本

输出 schema 与 enrich_images.py / video_compose.py 兼容。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import exa_client
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


PICK_CANDIDATES_SYSTEM = """你是 AI 频道总编。给你一批 Exa 搜回来的候选文章（含标题/URL/站点/日期/摘要/亮点片段），请按 AI 圈真实热度挑出 **{n} 篇** 适合改编为短视频的{lang_label}长文/深度报道。

挑选标准（按重要性）：
1) AI 全网真实热度（HN 高分、X 多人转、多家媒体同步报道、Reddit/Newsletter 头条、知乎/微博/即刻热门、公众号 10w+ 等）。题材小众/偏学术也 OK，只看热不热。
2) 自带完整叙事或核心观点（纯产品发布稿、纯参数更新、纯公关博客 pass）。
3) 必须是 N 件不同的事；同一事件的多家报道只留最权威/最热那一版。
4) 必须是真实可访问的{lang_label}文章 URL，不是推文/视频。

只输出严格 JSON 数组，长度恰好 {n}。"""

PICK_CANDIDATES_USER = """以下是 Exa {lang_label}候选池（JSON 数组）：

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
) -> list[dict]:
    """让 Opus 在给定（中文或英文）池里挑 n 篇候选。"""
    if not pool:
        return []
    print(f"  📥 Exa {lang_label}池共 {len(pool)} 条（去重去 exclude 后），让 {text_model()} 筛 {n} 篇…")
    exclude_section = ""
    if exclude_urls:
        joined = "\n  - ".join(exclude_urls)
        exclude_section = f"\n【硬性排除】不要再选这些 URL：\n  - {joined}"
    user_msg = PICK_CANDIDATES_USER.format(
        pool_json=_format_pool_for_opus(pool),
        exclude_section=exclude_section,
        n=n,
        lang_label=lang_label,
        lang_code=lang_code,
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
) -> tuple[list[dict], str | None]:
    """中英文各搜一池，分别挑 per_lang 篇，合并返回。"""
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
        exclude_urls=exclude_urls,
    )
    cands_zh = _pick_from_pool(
        pool_zh, n=per_lang, lang_code="zh", lang_label="中文",
        exclude_urls=exclude_urls,
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


PICK_BEST_SYSTEM = """你是 AI 频道的总编。从 3 篇候选英文长文里挑 1 篇做短视频。

**唯一挑选标准：AI 圈真实热度**。
- 主看：HN 分数、X/Twitter 转发量、主流媒体同步报道、Reddit 顶帖、newsletter 头条收录数。
- 同等热度时再看：自带叙事是否完整、有没有具体数字/事件锚点。
- **不要**因为题材偏学术/偏哲学/偏小众就降权——真正热的就是好的。

输出严格 JSON。"""

PICK_BEST_USER = """以下是 3 篇候选（JSON）。请按上述标准挑出**当周 AI 圈热度最高**的 1 篇。

{candidates_json}

只输出一个 JSON 对象（不要 markdown，不要解释）：
{{
  "pick_index": 1-based 整数,
  "reason": "选它的核心理由（25-50 字，必须提到具体热度证据）",
  "ranking": [候选序号按从热到冷排列],
  "rejected_reasons": {{ "2": "为何不选 25 字内", "3": "..." }}
}}"""


def auto_pick_best_article(candidates: list[dict]) -> tuple[dict, dict]:
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
        })
    user_msg = PICK_BEST_USER.format(
        candidates_json=json.dumps(cand_view, ensure_ascii=False, indent=2)
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
    print(f"  ✓ 评审结果：选 [{idx}] — {decision.get('reason', '')}")
    if decision.get("ranking"):
        print(f"    排名: {decision['ranking']}")
    rej = decision.get("rejected_reasons") or {}
    for k, v in rej.items():
        print(f"    舍弃 [{k}]: {v}")
    return candidates[idx - 1], decision


def pick_article(candidates: list[dict], *, auto: bool = False) -> tuple[dict, dict | None]:
    _print_candidates(candidates)
    if auto:
        article, decision = auto_pick_best_article(candidates)
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


def deep_read_article(article: dict, *, agent_id: str | None) -> tuple[dict, str | None]:
    """Exa 抓全文 → Opus 4.7 抽细节，返回细节字典。"""
    full_text = _fetch_article_text(article.get("url", ""))
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
ADAPT_SCRIPT_PROMPT = """你是抖音「白板手绘 + 段子手」科普编剧。用户消息会给你一篇长文的 metadata（原文可能是中文或英文）+ 由研究员深度读完原文后整理的「原文深读细节」（含段落 outline、所有数字、所有引语、人物、场景、原文真实开头/结尾、作者立场等）。请把它**忠实地改编**成一个中文短视频脚本（3-10 页可变）。

【素材使用原则】
★ 你的所有内容**必须**能在用户消息的「原文深读细节」里找到出处（outline / all_quotes / all_numbers / concrete_scenes / people / actual_opening / actual_ending）。
★ 优先使用 `concrete_scenes` 和 `all_quotes` 做钩子；优先使用 `all_numbers` 做硬数据页；末页顺着 `actual_ending` / `author_stance` 的原意写。
★ 如果某页找不到对应素材，**砍掉那页**而不是兜话。

==================================================
【第一原则 · 忠于原文，不要硬塞模板】
==================================================
★ **以文章自身的逻辑结构来拆页**，不是硬套 cover/insight/data/story/outro 五段式。
   - 如果原文是「事件 → 解读 → 数据 → 反思」就拆 4 页。
   - 如果原文是「钩子 → 三个论据 → 反例 → 结论 → 余波」就拆 6 页。
   - 如果原文足够丰富信息密度高，可以拆到 8-10 页。**最少 3 页，最多 10 页。**
   - 每一页都必须对应原文里**真实存在的一段内容**（一个段落、一个论点、一个数据、一个场景）。**不要凭空补段，不要为了凑页数兑水。**

★ **观点 / 事实 / 数字必须来自原文**：你的工作是翻译 + 转写为口语 + 选画面，不是另写一篇。
   - 允许你在解释术语时打小比方（一句话以内）。
   - 允许你重组顺序、合并重复段落、砍掉不重要的内容。
   - **不允许**虚构原文里没有的事实、数字、引语；**不允许**硬塞作者没说过的观点。

★ **不要套"关你啥事"模板**。结尾听文章自己的——
   - 如果原文以反问 / 警示 / 留白结尾，你也照做。
   - 如果原文以一句金句结尾，把那句翻成中文上屏。
   - 如果原文有明确建议，老老实实给建议，不要强行扯到「打工人」。
   - 末尾**可以**有评论引子，但**只有在自然的时候**才加；不要刻意。

==================================================
【页数与节奏】
==================================================
- **第 1 页（cover）**：钩子 + 用大白话讲清这篇文章在讲什么事 / 什么观点。必须让没读过原文的人 3 秒内 get 到主题。
- **中间页（body）**：每页只讲一个论点 / 一个数字组 / 一个场景。**信息密度限制：每页只允许 1 个新名词或 1 组新数字。**
- **最后一页**：跟着原文走，不强行套模板。可以是结论、可以是反问、可以是悬念。

【每页 narration 字数】
- cover：50-90 字
- 中间页：80-150 字（信息密度高的可以到 180 字）
- 末页：50-100 字
- 单句 ≤ 25 字，能拆就拆。主语别省。

【文风】
- 像跟朋友讲，不是念新闻稿。砍掉新闻腔（"援引""信源""文章认为""据 XX 报道"等）。
- 全片最多 1 处「据原文」类客观引述，且不在 cover。
- 每页第一句承接上一页（除 cover 外，必须有 lead_in 衔接锚点）。
- 网感词全片最多 2 处。能不用就不用。
- 每个新名词出现后，**下一句必须用一句白话解释**。

==================================================
【画面】
==================================================
风格固定：**笔记本方格纸 + 黑色钢笔手绘示意图 + 中文手写注释**（类似李永乐 / 3Blue1Brown 中文版）。
五种构图任选：对比图 / 流程图 / 类比图 / 数据图 / 时间轴。

`image_prompt`：**英文**，描述这页的手绘构图（不写风格词，模板会统一加）。
`on_image_text`：**中文**短语数组，3-10 条，每条 ≤ 12 字。是图上能看到的标签，不是复述 narration。

==================================================
【每页字段】
==================================================
- `layout`：第 1 页填 "cover"，其余全部填 "body"
- `chapter_title`：3-5 字章节短名
- `concept`：≤25 字，本页一句话
- `lead_in`：≤14 字衔接锚点（cover 可省，其余必填）
- `headline`：上屏中文标题（6-14 字）
- `narration`：口播原文（按上面字数要求）
- `image_prompt`：英文画面描述
- `on_image_text`：3-10 条中文标签数组
- 仅 cover 额外有 `subtitle`（8-22 字，悬念或核心观点）

==================================================
【顶层字段】
==================================================
- `title`：6-14 字中文标题（视频标题，不必跟原文标题一字不差，但必须传达原文核心）
- `keyword`：2-8 字中文关键词（从原文里抽一个最贴切的）
- `source`：{ "title": 原文标题（保留原文语言）, "url": 原文 URL, "site": 站点 }
- `slides`：数组，**长度 3-10**

==================================================
【输出】
==================================================
只输出一个 JSON 对象，不要 markdown，不要解释。

写完后**自查**：
① 每一页是否都对应原文里真实存在的内容？
② 有没有为了凑页数兑水的段落？有就删。
③ 有没有虚构原文里没有的数字 / 引语 / 观点？有就改。
④ Cover 第一句是不是钩子？
⑤ 末页是不是顺着原文自己的结尾，而不是硬套"关你啥事"？
"""


# ============================================================
# 校验：宽松版（页数 3-10、layout 只分 cover/body）
# ============================================================
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
        "请严格根据上面的「原文深读细节」改编：每页内容必须能在 outline / all_quotes / "
        "all_numbers / concrete_scenes 里找到出处；末页要顺着 actual_ending / author_stance 的"
        "原意，不要硬套模板。\n\n请输出 **严格 JSON 对象**（不要 markdown，不要解释）。"
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

    last_err: Exception | None = None
    raw_text = ""
    for attempt in range(3):
        try:
            raw_text = chat_complete(
                system=system_prompt,
                user=user_msg,
                max_tokens=8000,
            )
            raw = extract_json(raw_text, require_slides=True)
            data = merge_article_into_script(raw, article)
            return validate_article_script(data, article), agent_id
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            if attempt >= 2:
                break
            print(f"  ⚠️  校验未通过，让 {text_model()} 修正… ({e})", file=sys.stderr)
            fix_msg = ADAPT_FIX_PROMPT.format(errors=str(e), url=article.get("url", ""))
            if attempt == 0 and raw_text:
                fix_msg += f"\n\n上一轮输出：\n{raw_text[:12000]}"
            user_msg = (
                f"{user_msg}\n\n========\n【修正提示】\n{fix_msg}"
            )
        except RuntimeError as e:
            last_err = e
            if attempt >= 2:
                break
            print(f"  ⚠️  网络/接口失败，重试… ({e})", file=sys.stderr)
    raise RuntimeError(f"改编脚本失败（已重试）: {last_err}") from last_err


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
) -> tuple[dict, str]:
    logs_dir = logs_dir or (ROOT / "logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    selection_path = logs_dir / "last_article.json"
    saved: dict | None = None
    if use_selection and selection_path.is_file():
        try:
            saved = json.loads(selection_path.read_text(encoding="utf-8"))
            if not _article_looks_ok(saved):
                saved = None
        except json.JSONDecodeError:
            saved = None

    if saved:
        article = saved
        print("[1a] 跳过找文章，复用 logs/last_article.json")
    else:
        print(f"[1a] 搜索过去 {days} 天 AI 圈热点长文（中英文各 3 候选）…")
        candidates, agent_id = find_articles(
            days=days, exclude_urls=exclude_urls, agent_id=agent_id,
        )
        (logs_dir / "last_article_candidates.json").write_text(
            json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        article, decision = pick_article(candidates, auto=auto_pick)
        if decision:
            (logs_dir / "last_article_decision.json").write_text(
                json.dumps(decision, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    print(f"  ✓ 选定: {article.get('title')}")
    print(f"    站点: {article.get('site')}  日期: {article.get('published_at')}")
    print(f"    URL : {article.get('url')}")
    selection_path.write_text(
        json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    details_path = logs_dir / "last_article_details.json"
    if use_selection and saved and details_path.is_file():
        details = json.loads(details_path.read_text(encoding="utf-8"))
        print("[1b] 复用 last_article_details.json")
    else:
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
        description="文章驱动调研：Cursor 找/深读英文长文 → Opus 4.7 评审/改编中文短视频脚本"
    )
    parser.add_argument("-o", "--output", default=str(ROOT / "logs" / "last_script.json"))
    parser.add_argument("--days", type=int, default=7, help="搜索时间窗（天），默认 7")
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

    print(f"[research] 文章驱动 | 近 {args.days} 天 | 中英文各 3 候选 | "
          f"搜索=Exa | 筛选/深读/改编={text_model()} (effort=low)")

    try:
        script, _ = run_article_research(
            output=args.output,
            days=args.days,
            exclude_urls=exclude_urls or None,
            agent_id=args.agent_id,
            logs_dir=logs_dir,
            use_selection=args.use_selection,
            auto_pick=args.auto_pick,
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
