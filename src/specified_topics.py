#!/usr/bin/env python3
"""指定话题模式：把命令行里输入的一段话拆成多个话题，逐个生成 (article, details)。

输入示例（一段话，含编号）：
  1 小鹏财报，2 韬定律是什么，3 opus4.8发布，
  4 蔚小理净现金对比：<这里跟一大段已经整理好的内容>

每个话题分三种处理：
  · 自带内容（编号后跟「：」加一大段文字）→ 直接把这段文字当原文深读改编；
  · 普通话题词（如「小鹏财报」「opus4.8发布」）→ 用 Cursor/Exa 搜热门文章，
    深读最相关的一篇；科普向话题（如「韬定律是什么」）优先搜通俗科普文；
  · 搜不到合适文章 → 让模型用自身可靠知识写科普细节。

产出的 (article, details) 直接喂给 research.adapt_article_to_script / run_article_research。
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone

import categories
import research


# ============================================================
# 输入解析
# ============================================================
# 编号标记：行首或分隔符后的「数字 + 空格/、」。要求数字后是空白或顿号，
# 以免把 "opus4.8"、"4.279亿" 这类版本号/小数误判为编号。
_MARKER_RE = re.compile(r"(?:^|(?<=[，,。；;、\s]))(\d{1,2})[ \t　、]+")

# 行首可选编号（如「1 」「1.」「1、」「1)」），数字后必须跟分隔符才算编号，
# 避免把 "5G"、"4090显卡" 这类开头误删。用于「每行一个话题」模式。
_LEADING_MARKER_RE = re.compile(r"^\s*\d{1,2}[ \t　.、)）]+\s*")

# 自带内容的判定阈值（话题文字超过该长度，或「：」后内容超过该长度，视为自带内容）
_CONTENT_MIN_CHARS = 40


def _split_title_and_content(segment: str) -> tuple[str, str | None]:
    """把一个话题段落拆成 (标题线索, 自带内容或 None)。"""
    segment = segment.strip()
    if not segment:
        return "", None
    # 优先在第一个冒号处切：冒号前是标题线索，冒号后若较长则是自带内容
    m = re.search(r"[:：]", segment)
    if m:
        head = segment[: m.start()].strip()
        body = segment[m.end():].strip()
        if head and len(body) >= _CONTENT_MIN_CHARS:
            return head, body
    # 没有冒号但整段很长 → 整段当内容，标题取开头一小截
    if len(segment) >= max(60, _CONTENT_MIN_CHARS):
        head = re.split(r"[，,。\n]", segment, 1)[0].strip()[:24] or segment[:24]
        return head, segment
    # 普通搜索话题：只取第一个句子/换行前的部分，丢掉用户夹带的说明性文字
    head = re.split(r"[。！？\n]", segment, 1)[0].strip()
    return (head or segment), None


def parse_topics_input(text: str) -> list[dict]:
    """把一段含编号的话拆成话题列表。

    返回 [{"index", "raw", "title_hint", "provided_content"}]。
    若没识别到编号，则整段作为单个话题。
    """
    text = (text or "").strip()
    if not text:
        return []
    # 「每行一个话题」模式（最稳）：多行输入时，每个非空行就是一个话题，
    # 行内空格不影响拆分；行首可选编号会被剥掉。这样彻底规避输入法夹带空格导致的错拆。
    raw_lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(raw_lines) > 1:
        segments = [_LEADING_MARKER_RE.sub("", ln).strip() or ln for ln in raw_lines]
        topics: list[dict] = []
        for i, seg in enumerate(segments, 1):
            category, seg = categories.extract_category_tag(seg)
            title_hint, provided = _split_title_and_content(seg)
            if not title_hint:
                continue
            topics.append({
                "index": i,
                "raw": seg,
                "title_hint": title_hint,
                "provided_content": provided,
                "category": category,
            })
        return topics
    matches = list(_MARKER_RE.finditer(text))
    segments = []
    if not matches:
        # 没写编号：按顿号/分号/换行兜底拆分；拆不出多个就当成单个话题
        parts = [p.strip() for p in re.split(r"[、；;\n]+", text) if p.strip()]
        segments = parts if len(parts) > 1 else [text]
    else:
        for i, m in enumerate(matches):
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            seg = text[start:end].strip().rstrip("，,。；;、 ")
            if seg:
                segments.append(seg)
    topics: list[dict] = []
    for i, seg in enumerate(segments, 1):
        category, seg = categories.extract_category_tag(seg)
        title_hint, provided = _split_title_and_content(seg)
        if not title_hint:
            continue
        topics.append({
            "index": i,
            "raw": seg,
            "title_hint": title_hint,
            "provided_content": provided,
            "category": category,
        })
    return topics


# ============================================================
# 话题 → (article, details)
# ============================================================
_SCIENCE_HINT_RE = re.compile(r"是什么|什么是|原理|定律|定理|为什么|怎么|如何|科普|概念|意思|含义")


def _looks_like_science(title_hint: str) -> bool:
    return bool(_SCIENCE_HINT_RE.search(title_hint or ""))


# 通用财经/口语词：这些词不具区分度，不能用来判断候选文章是否"就是这个话题"
_GENERIC_BIGRAMS = {
    "涨疯", "疯了", "暴涨", "大涨", "上涨", "涨停", "连板", "飙升", "拉升", "新高",
    "市值", "超过", "突破", "亿元", "万亿", "千亿", "百亿", "股价", "股票", "个股",
    "营收", "净利", "利润", "财报", "业绩", "估值", "盘中", "收盘", "开盘", "成交",
    "为何", "为什么", "原因", "分析", "深度", "最新", "今日", "今天", "消息", "新闻",
}
# 抽词时直接丢弃的通用单字噪声
_GENERIC_CHARS = set("的了是和与及在为对从把被让向涨股亿元万千百多少高低大小")


def _topic_keywords(title_hint: str) -> set[str]:
    """从话题线索里抽取"有区分度"的实体关键词（CJK 2-gram + 英文词），
    去掉编号、数字、通用财经/口语词，用于判断候选文章是否真的讲这个话题。"""
    text = title_hint or ""
    keys: set[str] = set()
    # 英文/数字混合词（如 Anthropic、opus4、5G）：长度>=2 视为实体
    for w in re.findall(r"[A-Za-z][A-Za-z0-9.]{1,}", text):
        if len(w) >= 2:
            keys.add(w.lower())
    # CJK 连续片段 → 2-gram
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg in _GENERIC_BIGRAMS:
                continue
            if bg[0] in _GENERIC_CHARS and bg[1] in _GENERIC_CHARS:
                continue
            keys.add(bg)
    return keys


def _relevance_score(cand: dict, keys: set[str]) -> int:
    """候选文章与话题关键词的相关度：命中标题计 3 分，命中正文/摘要计 1 分。"""
    if not keys:
        return 0
    title = str(cand.get("title") or "")
    body = " ".join(str(cand.get(k) or "") for k in ("summary_zh", "summary", "text", "thesis"))
    score = 0
    for k in keys:
        if k in title or k.lower() in title.lower():
            score += 3
        elif k in body or k.lower() in body.lower():
            score += 1
    return score


def _exa_queries_for_topic(title_hint: str) -> list[str]:
    base = [title_hint]
    if _looks_like_science(title_hint):
        base += [
            f"{title_hint} 通俗 科普 解释",
            f"{title_hint} explained simply",
        ]
    else:
        base += [
            f"{title_hint} 最新 分析 深度",
            f"{title_hint} latest analysis explained",
        ]
    return base


def _search_candidates(title_hint: str, *, days: int) -> list[dict]:
    try:
        pool = research._exa_search_pool(
            days=days,
            exclude_urls=None,
            queries=_exa_queries_for_topic(title_hint),
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️  Exa 搜索「{title_hint}」失败：{exc}", file=sys.stderr)
        return []
    cands = [research._exa_result_to_candidate(r) for r in pool]
    # 先按"是否真的讲这个话题"打相关度分，避免取到一篇只是日期最新、实质无关的文章。
    keys = _topic_keywords(title_hint)
    for c in cands:
        c["_relevance"] = _relevance_score(c, keys)
    if keys:
        relevant = [c for c in cands if c.get("_relevance", 0) > 0]
        if not relevant:
            print(f"  ⚠️  搜到 {len(cands)} 条，但没有一篇与「{title_hint}」实体（{ '/'.join(sorted(keys)) }）相关")
            return []
        cands = relevant
    science = _looks_like_science(title_hint)
    if science:
        # 科普型：相关度优先即可，时效不敏感。
        cands.sort(key=lambda c: c.get("_relevance", 0), reverse=True)
    else:
        # 新闻/财报型：先保证"标题命中实体"，再尽量取最新——指定话题都是"今天的热点"。
        cands.sort(
            key=lambda c: (c.get("_relevance", 0) >= 3, str(c.get("published_at") or ""), c.get("_relevance", 0)),
            reverse=True,
        )
    if cands:
        top = cands[0]
        print(f"  🎯 相关度+时效优先，命中候选：[{top.get('_relevance')}分] {top.get('published_at')} {top.get('title')}")
    return cands


def _days_old(cand: dict) -> float:
    """候选文章距今多少天；无法解析日期时返回一个很大的值（视为很旧）。"""
    raw = str(cand.get("published_at") or "").strip()
    if not raw:
        return 1e9
    # 统一取前 10 位的日期部分（YYYY-MM-DD / YYYY/MM/DD），足够判断时效。
    m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", raw)
    if not m:
        return 1e9
    try:
        dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    except ValueError:
        return 1e9
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0


def _materials_block(cands: list[dict], *, limit: int = 6) -> str:
    """把若干相关候选拼成"参考材料"，供模型综合改写时使用。"""
    lines: list[str] = []
    for i, c in enumerate(cands[:limit], 1):
        title = str(c.get("title") or "").strip()
        date = str(c.get("published_at") or "").strip()
        site = str(c.get("site") or "").strip()
        body = str(c.get("summary_zh") or c.get("summary") or c.get("text") or "").strip()
        body = re.sub(r"\s+", " ", body)[:400]
        head = " | ".join(p for p in (date, site) if p)
        lines.append(f"[材料{i}] {title}（{head}）\n{body}")
    return "\n\n".join(lines)


def _article_from_topic(topic: dict) -> dict:
    """自带内容/自写时的合成 article（无来源 URL）。"""
    title_hint = topic["title_hint"]
    return {
        "title": title_hint,
        "question_title": "",
        "url": "",
        "site": "",
        "author": "",
        "published_at": "",
        "language": "zh",
        "summary_zh": title_hint,
        "thesis": title_hint,
        "key_facts": [title_hint],
        "narrative_arc": "科普讲解",
        "source_type": "specified",
        "_no_source": True,
    }


def build_topic_research(
    topic: dict,
    *,
    days: int = 120,
) -> tuple[dict, dict]:
    """把一个话题变成 (article, details)。

    1) 自带内容 → 把内容当原文深读；
    2) 普通话题 → Exa 搜文章，深读最相关一篇；
    3) 搜不到 → 模型用自身知识写科普细节。
    """
    title_hint = topic["title_hint"]
    provided = topic.get("provided_content")

    # —— 路线 1：用户自带内容 ——
    if provided:
        print(f"  📝 「{title_hint}」自带内容（{len(provided)} 字），直接深读改编")
        article = _article_from_topic(topic)
        try:
            details, _ = research.deep_read_article(article, agent_id=None, full_text=provided)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️  深读自带内容失败，转用模型整理：{exc}", file=sys.stderr)
            details = research.author_details_from_knowledge(title_hint, provided_content=provided)
        return article, details

    # —— 路线 2：搜文章 ——
    science = _looks_like_science(title_hint)
    fresh_days = float(os.environ.get("AIVIDEO_TOPIC_FRESH_DAYS", "2"))
    print(f"  🔍 「{title_hint}」联网搜索热门文章…")
    candidates = _search_candidates(title_hint, days=days)

    # 新闻/财报型：指定话题默认是"今天的热点"，必须足够新。
    # 若搜到的最新一篇也偏旧（超过 fresh_days），不硬套旧数据，转为综合多篇材料 + 模型最新认知自写。
    if candidates and not science:
        freshest = min(_days_old(c) for c in candidates)
        if freshest > fresh_days:
            print(
                f"  🕒 最新候选距今约 {freshest:.1f} 天（阈值 {fresh_days:.0f} 天），"
                f"判定不够及时 → 综合 {min(len(candidates), 6)} 篇材料让模型按最新情况自写",
                file=sys.stderr,
            )
            return _synthesize_from_materials(topic, candidates)

    for cand in candidates[:5]:
        url = str(cand.get("url") or "")
        if not url.startswith("http"):
            continue
        try:
            details, _ = research.deep_read_article(cand, agent_id=None)
        except Exception as exc:  # noqa: BLE001
            print(f"  ↻ 深读 {url} 失败，换下一篇：{exc}", file=sys.stderr)
            continue
        cand.setdefault("question_title", "")
        return cand, details

    # —— 路线 3：没有相关文章 → 综合材料 / 模型自身知识自写 ——
    if candidates:
        print(f"  ✍️  「{title_hint}」相关文章都无法深读，改用综合材料自写", file=sys.stderr)
        return _synthesize_from_materials(topic, candidates)
    print(f"  ✍️  「{title_hint}」没搜到相关文章，改用模型自身最新认知自写", file=sys.stderr)
    article = _article_from_topic(topic)
    details = research.author_details_from_knowledge(title_hint)
    return article, details


def _synthesize_from_materials(topic: dict, candidates: list[dict]) -> tuple[dict, dict]:
    """把若干相关候选当"参考材料"，让模型结合自身最新认知综合改写。
    用于"指定话题但没有足够及时的单篇文章"的场景。"""
    title_hint = topic["title_hint"]
    materials = _materials_block(candidates)
    article = _article_from_topic(topic)
    # 标注为综合材料来源，并把最相关一篇的来源信息带上，便于展示。
    if candidates:
        article["site"] = str(candidates[0].get("site") or "")
    article["narrative_arc"] = "综合最新材料讲解"
    try:
        details = research.author_details_from_knowledge(
            title_hint, provided_content=materials, reference_only=True
        )
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️  综合材料自写失败，转用纯模型知识：{exc}", file=sys.stderr)
        details = research.author_details_from_knowledge(title_hint)
    return article, details
