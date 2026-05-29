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

import re
import sys

import research


# ============================================================
# 输入解析
# ============================================================
# 编号标记：行首或分隔符后的「数字 + 空格/、」。要求数字后是空白或顿号，
# 以免把 "opus4.8"、"4.279亿" 这类版本号/小数误判为编号。
_MARKER_RE = re.compile(r"(?:^|(?<=[，,。；;、\s]))(\d{1,2})[ \t　、]+")

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
    matches = list(_MARKER_RE.finditer(text))
    segments: list[str] = []
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
        title_hint, provided = _split_title_and_content(seg)
        if not title_hint:
            continue
        topics.append({
            "index": i,
            "raw": seg,
            "title_hint": title_hint,
            "provided_content": provided,
        })
    return topics


# ============================================================
# 话题 → (article, details)
# ============================================================
_SCIENCE_HINT_RE = re.compile(r"是什么|什么是|原理|定律|定理|为什么|怎么|如何|科普|概念|意思|含义")


def _looks_like_science(title_hint: str) -> bool:
    return bool(_SCIENCE_HINT_RE.search(title_hint or ""))


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
    # 新闻/财报型话题（如「小鹏财报」「opus4.8发布」）优先最新一篇，避免抓到几个月前的旧财报；
    # 科普型话题（「是什么/原理」）保持相关性排序。
    if not _looks_like_science(title_hint):
        cands.sort(key=lambda c: str(c.get("published_at") or ""), reverse=True)
        if cands:
            print(f"  📅 按时间优先，最新候选：{cands[0].get('published_at')} {cands[0].get('title')}")
    return cands


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
    print(f"  🔍 「{title_hint}」联网搜索热门文章…")
    candidates = _search_candidates(title_hint, days=days)
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

    # —— 路线 3：模型自写 ——
    print(f"  ✍️  「{title_hint}」没搜到合适文章，改用模型自身知识写科普", file=sys.stderr)
    article = _article_from_topic(topic)
    details = research.author_details_from_knowledge(title_hint)
    return article, details
