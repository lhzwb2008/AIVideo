#!/usr/bin/env python3
"""跨批次选题历史：article_history.json 读写与主题去重辅助。"""

from __future__ import annotations

import json
import os
import re
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


def _title_from_script_json(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    script = data.get("script") if isinstance(data.get("script"), dict) else data
    if not isinstance(script, dict):
        return ""
    for key in ("title", "question_title"):
        t = str(script.get(key) or "").strip()
        if t:
            return t
    article = data.get("article") or script.get("article") or {}
    if isinstance(article, dict):
        return str(article.get("title") or article.get("question_title") or "").strip()
    return ""


def history_retention_days() -> int:
    """历史文件保留天数；至少覆盖科普去重窗口。"""
    raw = os.environ.get("AIVIDEO_HISTORY_RETENTION_DAYS", "").strip()
    if raw:
        try:
            return max(90, int(raw))
        except ValueError:
            pass
    try:
        from theme_clusters import edu_dedup_days

        return max(365, edu_dedup_days())
    except Exception:  # noqa: BLE001
        return 3650


def published_titles_for_dedup(days: int = 90, limit: int = 500) -> list[str]:
    """合并 article_history、归档脚本与 output 脚本，供科普选题去重。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
    out: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        t = (raw or "").strip()
        if not t or t in seen:
            return
        seen.add(t)
        out.append(t)

    for item in recent_history(days):
        for key in ("script_title", "title", "title_hint", "article_title", "question_title"):
            _add(str(item.get(key) or ""))

    def _mtime_ok(p: Path) -> bool:
        try:
            return datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc) >= cutoff
        except OSError:
            return False

    from locale_env import iter_script_json_paths, locale_logs_dir

    for path in iter_script_json_paths():
        if not _mtime_ok(path):
            continue
        _add(_title_from_script_json(path))

    log_dir = locale_logs_dir("zh")
    for log_name in (
        "last_xueqiu_publish.json",
        "last_eastmoney_publish.json",
        "last_bilibili_publish.json",
        "weekend_edu_last_pick.json",
        "make_publish_last.json",
    ):
        log_path = log_dir / log_name
        if not log_path.is_file() and (ROOT / "logs" / log_name).is_file():
            log_path = ROOT / "logs" / log_name
        if not log_path.is_file():
            continue
        try:
            payload = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        _add(str(payload.get("title") or ""))
        for topic in payload.get("topics") or []:
            if isinstance(topic, dict):
                _add(str(topic.get("title") or topic.get("title_hint") or ""))
        for item in payload.get("made") or []:
            if isinstance(item, dict):
                _add(str(item.get("title") or item.get("title_hint") or ""))

    # 视频清单里的脚本标题
    for manifest_name in ("video_manifest.jsonl",):
        manifest = log_dir / manifest_name
        if not manifest.is_file():
            continue
        try:
            for line in manifest.read_text(encoding="utf-8").splitlines()[-200:]:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    continue
                script_rel = str(row.get("script") or "").strip()
                if script_rel:
                    sp = Path(script_rel)
                    if not sp.is_file():
                        sp = ROOT / script_rel
                    if sp.is_file() and _mtime_ok(sp):
                        _add(_title_from_script_json(sp))
                _add(str(row.get("title") or ""))
        except (OSError, json.JSONDecodeError):
            pass

    history_jsonl = log_dir / "xueqiu_publish_history.jsonl"
    if not history_jsonl.is_file():
        history_jsonl = ROOT / "logs" / "xueqiu_publish_history.jsonl"
    if history_jsonl.is_file():
        try:
            for line in history_jsonl.read_text(encoding="utf-8").splitlines()[-80:]:
                if not line.strip():
                    continue
                row = json.loads(line)
                if isinstance(row, dict):
                    _add(str(row.get("title") or ""))
        except (OSError, json.JSONDecodeError):
            pass

    archive_root = ROOT / "archive" / "published"
    if archive_root.is_dir():
        json_paths = sorted(
            archive_root.glob("**/zh/**/*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for path in json_paths:
            if not _mtime_ok(path):
                continue
            _add(_title_from_script_json(path))
            if len(out) >= limit:
                break

    readme_paths = sorted(
        archive_root.glob("**/zh/**/README.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    ) if archive_root.is_dir() else []
    for path in readme_paths:
        if not _mtime_ok(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for block in re.finditer(r"```\s*\n([^`]+?)```", text, re.S):
            line = block.group(1).strip().splitlines()[0].strip()
            if line and len(line) <= 80:
                _add(line)
        if len(out) >= limit:
            break

    return out[:limit]


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
    for key in (
        "script_title", "article_title", "question_title", "category", "direction",
        "topic_slot", "topic_id", "mode", "theme_cluster", "edu_concept", "title_hint",
    ):
        value = str(item.get(key) or "").strip()
        if value:
            record[key] = value
    if url:
        record["url"] = url
    # 同标题短时间内不重复写入
    for prev in reversed(items[-8:]):
        if str(prev.get("script_title") or prev.get("title") or "").strip() == (
            record.get("script_title") or record.get("title") or ""
        ):
            prev_at = str(prev.get("made_at") or "")
            try:
                ts = datetime.fromisoformat(prev_at.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - ts < timedelta(hours=6):
                    return
            except ValueError:
                pass
            break
    items.append(record)
    items = [x for x in items if _within_window(x, days=history_retention_days())]
    hist = _history_file()
    hist.parent.mkdir(parents=True, exist_ok=True)
    hist.write_text(
        json.dumps({"items": items, "updated_at": datetime.now(timezone.utc).isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_history_from_script(script_path: Path, video: Path | None = None) -> None:
    """单条制作完成后写历史，供下一条主题去重（含 --no-publish）。"""
    del video  # 保留参数兼容
    try:
        data = json.loads(script_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    article = data.get("article") or (data.get("script") or {}).get("article") or {}
    script = data.get("script") or data
    try:
        from theme_clusters import infer_edu_concept, infer_theme_cluster

        theme_cluster = str(script.get("theme_cluster") or "").strip()
        title_blob = (
            str(script.get("title") or ""),
            str(script.get("cold_open") or ""),
            str(script.get("angle") or ""),
            str(article.get("title") or ""),
            str(article.get("question_title") or ""),
        )
        edu_concept = str(script.get("edu_concept") or "").strip() or infer_edu_concept(*title_blob)
        if not theme_cluster:
            theme_cluster = edu_concept or infer_theme_cluster(*title_blob)
        if edu_concept and not theme_cluster.startswith("edu_"):
            theme_cluster = edu_concept
    except Exception:  # noqa: BLE001
        theme_cluster = str(script.get("theme_cluster") or "").strip()
        edu_concept = str(script.get("edu_concept") or "").strip()

    topic_plan = (data.get("article") or {}).get("_topic_plan") or {}
    if not edu_concept:
        edu_concept = str(topic_plan.get("edu_concept") or topic_plan.get("theme_cluster") or "").strip()
    record = {
        "url": article.get("url") or (script.get("source") or {}).get("url") or "",
        "title": article.get("title") or script.get("title") or "",
        "article_title": article.get("title") or "",
        "script_title": script.get("title") or "",
        "question_title": article.get("question_title") or "",
        "cold_open": str(script.get("cold_open") or "").strip(),
        "theme_cluster": theme_cluster,
        "edu_concept": edu_concept,
        "category": str(script.get("category") or "").strip(),
        "topic_slot": topic_plan.get("slot") or topic_plan.get("direction") or str(script.get("topic_slot") or "").strip(),
        "topic_id": str(topic_plan.get("topic_id") or script.get("topic_id") or "").strip(),
        "title_hint": str(topic_plan.get("title_hint") or "").strip(),
        "mode": "weekend_edu" if str(topic_plan.get("script_mode") or "") == "edu_explain" else "",
    }
    append_history({k: v for k, v in record.items() if v})
