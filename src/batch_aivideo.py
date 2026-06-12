#!/usr/bin/env python3
"""跨批次选题历史：article_history.json 读写与主题去重辅助。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paths import ROOT

HISTORY_FILE = ROOT / "logs" / "zh" / "article_history.json"  # 默认路径；读写请用 _history_file()
HISTORY_WINDOW_DAYS = int(
    os.environ.get(
        "BATCH_HISTORY_DAYS",
        os.environ.get("AIVIDEO_DAYS", os.environ.get("DAILY_RUN_DAYS", "3")),
    )
)


def _history_file() -> Path:
    from locale_env import locale_logs_dir

    return locale_logs_dir("zh") / "article_history.json"


def load_history() -> list[dict]:
    path = _history_file()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items") if isinstance(data, dict) else data
    return [x for x in (items or []) if isinstance(x, dict)]


def _within_window(item: dict, *, days: int) -> bool:
    made_at = str(item.get("made_at") or "").strip()
    if not made_at:
        return True
    try:
        ts = datetime.fromisoformat(made_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= cutoff


def recent_history(days: int = HISTORY_WINDOW_DAYS) -> list[dict]:
    return [x for x in load_history() if _within_window(x, days=days)]


def history_recent_topics(days: int = HISTORY_WINDOW_DAYS, limit: int = 30) -> list[str]:
    topics: list[str] = []
    seen: set[str] = set()
    for x in reversed(recent_history(days)):
        t = str(x.get("title") or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        topics.append(t)
        if len(topics) >= limit:
            break
    return topics


def append_history(item: dict) -> None:
    url = str(item.get("url") or "").strip()
    title = str(item.get("title") or "").strip()
    if not url and not title:
        return
    items = load_history()
    record = {
        "title": title,
        "made_at": datetime.now(timezone.utc).isoformat(),
    }
    for key in ("script_title", "article_title", "question_title", "category", "direction", "topic_slot"):
        value = str(item.get(key) or "").strip()
        if value:
            record[key] = value
    if url:
        record["url"] = url
    items.append(record)
    items = [x for x in items if _within_window(x, days=90)]
    hist = _history_file()
    hist.parent.mkdir(parents=True, exist_ok=True)
    hist.write_text(
        json.dumps({"items": items, "updated_at": datetime.now(timezone.utc).isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_history_from_script(script_path: Path, video: Path | None = None) -> None:
    """单条制作完成后写历史，供下一条主题去重。"""
    try:
        data = json.loads(script_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    article = data.get("article") or (data.get("script") or {}).get("article") or {}
    script = data.get("script") or data
    try:
        from theme_clusters import infer_theme_cluster

        theme_cluster = str(script.get("theme_cluster") or "").strip()
        if not theme_cluster:
            theme_cluster = infer_theme_cluster(
                str(script.get("title") or ""),
                str(script.get("cold_open") or ""),
                str(script.get("angle") or ""),
            )
    except Exception:  # noqa: BLE001
        theme_cluster = str(script.get("theme_cluster") or "").strip()

    record = {
        "url": article.get("url") or (script.get("source") or {}).get("url") or "",
        "title": article.get("title") or script.get("title") or "",
        "article_title": article.get("title") or "",
        "script_title": script.get("title") or "",
        "question_title": article.get("question_title") or "",
        "cold_open": str(script.get("cold_open") or "").strip(),
        "theme_cluster": theme_cluster,
        "category": str(script.get("category") or "").strip(),
        "topic_slot": (
            (data.get("article") or {}).get("_topic_plan") or {}
        ).get("direction") or str(script.get("topic_slot") or "").strip(),
    }
    append_history({k: v for k, v in record.items() if v})
