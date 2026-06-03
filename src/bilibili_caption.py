"""从脚本生成 B 站投稿字段（标题 / 简介 / 标签 / 分区）。"""

from __future__ import annotations

import os

from douyin_caption import _env, _strip_urls, build_sau_fields


def default_tid() -> int:
    raw = _env("BILIBILI_TID", "207")
    try:
        return int(raw)
    except ValueError:
        return 207


def build_bilibili_fields(script: dict | None) -> dict:
    """返回 {title, desc, tags, tid}。tags 为逗号分隔字符串（最多 12 个）。"""
    base = build_sau_fields(script)
    title = (base.get("title") or "AI财经热点")[:80]

    desc_bits = [base.get("desc") or title]
    suffix = _env("BILIBILI_DESC_SUFFIX")
    if suffix:
        desc_bits.append(_strip_urls(suffix))
    desc = _strip_urls(" ".join(desc_bits))[:2000]

    tag_parts: list[str] = []
    for part in (base.get("tags") or "").split(","):
        t = part.strip().lstrip("#")
        if t and t not in tag_parts:
            tag_parts.append(t)
    for raw in _env("BILIBILI_HASHTAGS", "#AI #财经 #股市 #美股").split():
        if len(tag_parts) >= 12:
            break
        t = raw.strip().lstrip("#")
        if t and t not in tag_parts:
            tag_parts.append(t)

    return {
        "title": title,
        "desc": desc,
        "tags": ",".join(tag_parts[:12]),
        "tid": default_tid(),
    }
