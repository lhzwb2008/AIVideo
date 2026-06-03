#!/usr/bin/env python3
"""旧批量制作入口。主流程请使用 ./make-and-publish.sh。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from paths import ROOT
from research import load_env, run_article_research

PROGRESS_FILE = ROOT / "logs" / "batch_progress.json"
BATCH_LOG = ROOT / "logs" / "batch_run.log"
HISTORY_FILE = ROOT / "logs" / "article_history.json"
# 去重窗口默认跟随搜索窗口（AIVIDEO_DAYS/DAILY_RUN_DAYS，默认 3 天），
# 这样「搜索近 N 天」与「去重保留 N 天」始终一致；需要时用 BATCH_HISTORY_DAYS 单独覆盖。
HISTORY_WINDOW_DAYS = int(
    os.environ.get(
        "BATCH_HISTORY_DAYS",
        os.environ.get("AIVIDEO_DAYS", os.environ.get("DAILY_RUN_DAYS", "3")),
    )
)

_COMPANY_ALIASES = {
    "pdd": ("pdd", "pdd holdings", "pinduoduo", "拼多多"),
    "baidu": ("bidu", "baidu", "百度"),
    "netease": ("ntes", "netease", "网易"),
    "kuaishou": ("kuaishou", "快手"),
    "bilibili": ("bili", "bilibili", "b站", "哔哩哔哩"),
    "alibaba": ("baba", "alibaba", "阿里", "阿里巴巴"),
    "tencent": ("tencent", "腾讯"),
    "jd": ("jd", "jd.com", "京东"),
    "nio": ("nio", "蔚来"),
    "li_auto": ("li auto", "li", "理想汽车", "理想"),
    "xpeng": ("xpeng", "xpev", "小鹏"),
    "meta": ("meta", "facebook"),
    "nvidia": ("nvidia", "nvda", "英伟达"),
    "alphabet": ("alphabet", "google", "googl", "谷歌"),
    "microsoft": ("microsoft", "msft", "微软"),
    "amazon": ("amazon", "amzn", "亚马逊"),
    "tesla": ("tesla", "tsla", "特斯拉"),
}

_EARNINGS_RE = re.compile(
    r"财报|业绩|营收|利润|盈利|亏损|earnings|results|revenue|profit|eps|guidance|quarter",
    re.I,
)
_STOCK_MOVE_RE = re.compile(r"股价|大涨|暴涨|大跌|暴跌|下跌|上涨|跌|涨|stock|shares?", re.I)
_QUARTER_RE = re.compile(r"\bq[1-4]\b|first quarter|second quarter|third quarter|fourth quarter|一季|二季|三季|四季|第[一二三四]季", re.I)


def log(msg: str) -> None:
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    BATCH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with BATCH_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_progress() -> dict:
    if PROGRESS_FILE.is_file():
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    return {
        "target": 10,
        "days": 7,
        "completed": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def save_progress(data: dict) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    PROGRESS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def exclude_urls(progress: dict) -> list[str]:
    urls: list[str] = []
    for item in progress.get("completed") or []:
        u = str(item.get("url") or "").strip()
        if u:
            urls.append(u)
    return urls


# ============================================================
# 跨批次/跨天主题去重：logs/article_history.json
# ============================================================
def load_history() -> list[dict]:
    if not HISTORY_FILE.is_file():
        return []
    try:
        data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
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


def history_exclude_urls(days: int = HISTORY_WINDOW_DAYS) -> list[str]:
    return [str(x.get("url") or "").strip() for x in recent_history(days) if x.get("url")]


def history_recent_topics(days: int = HISTORY_WINDOW_DAYS, limit: int = 30) -> list[str]:
    topics: list[str] = []
    seen: set[str] = set()
    for x in reversed(recent_history(days)):  # 最新的优先
        t = str(x.get("title") or "").strip()
        if not t or t in seen:
            continue
        seen.add(t)
        topics.append(t)
        if len(topics) >= limit:
            break
    return topics


def _topic_text(item: dict) -> str:
    parts = [
        item.get("title"),
        item.get("script_title"),
        item.get("article_title"),
        item.get("question_title"),
        item.get("summary_zh"),
        item.get("summary_en"),
        item.get("thesis"),
        item.get("score_reason"),
        item.get("reason"),
    ]
    facts = item.get("key_facts")
    if isinstance(facts, list):
        parts.extend(facts)
    return " ".join(str(x) for x in parts if str(x or "").strip()).lower()


def _title_text(item: dict) -> str:
    """只取标题类字段（文章主角），用于「同公司」判定，避免摘要里顺带提及的
    公司名（如 A股 半导体文造提到英伟达）把候选误判成该公司的重复。"""
    parts = [
        item.get("title"),
        item.get("script_title"),
        item.get("article_title"),
        item.get("question_title"),
    ]
    return " ".join(str(x) for x in parts if str(x or "").strip()).lower()


def _compact_topic_text(text: str) -> str:
    text = re.sub(r"https?://\S+", " ", text.lower())
    text = re.sub(r"[\W_]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _companies_in_text(text: str) -> set[str]:
    found: set[str] = set()
    haystack = f" {_compact_topic_text(text)} "
    raw = text.lower()
    for company, aliases in _COMPANY_ALIASES.items():
        for alias in aliases:
            alias_l = alias.lower()
            if re.search(r"[\u4e00-\u9fff]", alias_l):
                if alias_l in raw:
                    found.add(company)
                    break
            elif f" {alias_l} " in haystack:
                found.add(company)
                break
    return found


def _token_set(text: str) -> set[str]:
    stop = {
        "the", "and", "for", "with", "from", "this", "that", "what", "why",
        "stock", "price", "news", "quote", "history", "finance", "inc", "ltd",
        "公司", "视频", "什么", "为什么", "到底", "是否", "市场",
    }
    tokens = set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", _compact_topic_text(text)))
    return {t for t in tokens if t not in stop}


def duplicate_topic_reason(
    candidate: dict,
    recent_items: list[dict] | None = None,
    *,
    extra_cluster_counts: dict[str, int] | None = None,
) -> str:
    """本地兜底去重：概念簇、同公司+财报、标题高度相似。"""
    try:
        from theme_clusters import cluster_duplicate_reason

        cluster_reason = cluster_duplicate_reason(
            candidate, recent_items, extra_counts=extra_cluster_counts,
        )
        if cluster_reason:
            return cluster_reason
    except Exception:  # noqa: BLE001
        pass

    cand_text = _topic_text(candidate)
    if not cand_text:
        return ""
    # 「同公司」只认标题里的主角公司，不认摘要/key_facts 里顺带提及的，避免误杀。
    cand_companies = _companies_in_text(_title_text(candidate))
    cand_tokens = _token_set(cand_text)
    cand_has_earnings = bool(_EARNINGS_RE.search(cand_text))
    cand_has_stock_move = bool(_STOCK_MOVE_RE.search(cand_text))
    cand_has_quarter = bool(_QUARTER_RE.search(cand_text))
    for old in recent_items if recent_items is not None else recent_history():
        old_text = _topic_text(old)
        if not old_text:
            continue
        old_title = str(old.get("title") or old.get("script_title") or old.get("article_title") or "").strip()
        old_companies = _companies_in_text(_title_text(old))
        same_company = bool(cand_companies & old_companies)
        old_has_earnings = bool(_EARNINGS_RE.search(old_text))
        old_has_stock_move = bool(_STOCK_MOVE_RE.search(old_text))
        old_has_quarter = bool(_QUARTER_RE.search(old_text))
        if same_company and cand_has_earnings and old_has_earnings:
            if cand_has_quarter or old_has_quarter or cand_has_stock_move or old_has_stock_move:
                return f"同公司同财报/股价事件已做过：{old_title}"
        old_tokens = _token_set(old_text)
        # 相似度分支只在「同公司」时启用，且用 Jaccard（并集分母）避免短中文标题被
        # 少数通用财经词（营收/季度/增长等）误判为重复，从而错杀无公司关联的 A股 爆点。
        if same_company and len(cand_tokens) >= 4 and len(old_tokens) >= 4:
            inter = len(cand_tokens & old_tokens)
            jaccard = inter / max(1, len(cand_tokens | old_tokens))
            if jaccard >= 0.6:
                return f"标题/主题高度相似：{old_title}"
    return ""


def filter_duplicate_topics(candidates: list[dict], *, days: int = HISTORY_WINDOW_DAYS) -> list[dict]:
    recent = recent_history(days)
    kept: list[dict] = []
    for cand in candidates:
        reason = duplicate_topic_reason(cand, recent)
        if reason:
            title = cand.get("question_title") or cand.get("title")
            print(f"  ↯ 去重拦截：{title}（{reason}）")
            continue
        kept.append(cand)
    return kept


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
    # 兼容旧逻辑：URL 仅用于硬排除，同主题去重主要看 title。
    if url:
        record["url"] = url
    items.append(record)
    # 修剪：仅保留近 90 天，避免文件膨胀
    items = [x for x in items if _within_window(x, days=90)]
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps({"items": items, "updated_at": datetime.now(timezone.utc).isoformat()},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_history_from_script(script_path: Path, video: Path | None = None) -> None:
    """单条 run-aivideo 也写历史，保证下一条能主题去重。"""
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


def retry(step: str, fn, *, max_attempts: int, pause: int):
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"  ✗ {step} 第 {attempt}/{max_attempts} 次失败: {e}")
            if attempt < max_attempts:
                log(f"  ⏳ {pause}s 后重试…")
                time.sleep(pause)
    raise RuntimeError(f"{step} 在 {max_attempts} 次尝试后仍失败: {last_err}") from last_err


def rel_path(path: Path) -> str:
    """转为相对 ROOT 的路径（兼容 last_video.txt 里的相对路径）。"""
    resolved = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    return str(resolved.relative_to(ROOT.resolve()))


def run_compose(script_path: Path) -> Path:
    env = os.environ.copy()
    proc = subprocess.run(
        [str(ROOT / "scripts" / "run-compose.sh"), str(script_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "本地合成失败")

    last_video = ROOT / "logs" / "last_video.txt"
    if not last_video.is_file():
        raise RuntimeError("未找到 logs/last_video.txt")
    video = Path(last_video.read_text(encoding="utf-8").strip())
    if not video.is_file():
        raise RuntimeError(f"视频文件不存在: {video}")
    return video


def process_one(
    index: int,
    *,
    days: int,
    batch_total: int,
    exclude: list[str],
    max_retries: int,
    retry_pause: int,
    recent_topics: list[str] | None = None,
    source: str = "feeds",
    fresh_hours: int = 24,
) -> dict:
    batch_dir = ROOT / "logs" / "batch"
    batch_dir.mkdir(parents=True, exist_ok=True)
    script_path = batch_dir / f"{index:02d}_script.json"

    log(f"━━━ [{index}/{batch_total}] 调研（找文章+深读+改编）━━━")
    agent_id: str | None = None
    if (ROOT / "logs" / "cursor_agent.json").is_file():
        try:
            agent_id = json.loads((ROOT / "logs" / "cursor_agent.json").read_text())["agent_id"]
        except (json.JSONDecodeError, KeyError):
            agent_id = None

    def do_research() -> dict:
        nonlocal agent_id
        script, agent_id = run_article_research(
            output=script_path,
            days=days,
            exclude_urls=exclude or None,
            agent_id=agent_id,
            auto_pick=True,
            recent_topics=recent_topics or None,
            source=source,
            fresh_hours=fresh_hours,
        )
        return script

    script = retry("调研", do_research, max_attempts=max_retries, pause=retry_pause)
    log(f"  ✓ 脚本: {script.get('title')}")

    if os.environ.get("AIHUBMIX_API_KEY", "").strip():
        log(f"━━━ [{index}/{batch_total}] API 生图 ━━━")

        def do_enrich() -> None:
            proc = subprocess.run(
                [str(ROOT / "scripts" / "run-enrich-images.sh"), str(script_path)],
                cwd=ROOT,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "API 生图失败")
            for line in (proc.stderr or "").splitlines():
                if line.strip():
                    log(f"  {line}")

        retry("API 生图", do_enrich, max_attempts=max_retries, pause=retry_pause)
    else:
        log("  跳过 API 生图（未设置 AIHUBMIX_API_KEY）")

    log(f"━━━ [{index}/{batch_total}] 本地合成 ━━━")
    video = retry(
        "本地合成",
        lambda: run_compose(script_path),
        max_attempts=max_retries,
        pause=retry_pause,
    )
    log(f"  ✓ 视频: {rel_path(video)}")

    article_path = ROOT / "logs" / "last_article.json"
    article_url = ""
    if article_path.is_file():
        try:
            article_url = json.loads(article_path.read_text(encoding="utf-8")).get("url") or ""
        except json.JSONDecodeError:
            pass
    return {
        "index": index,
        "url": article_url,
        "title": script.get("title"),
        "script": rel_path(script_path),
        "video": rel_path(video),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="批量制作并发布 AI 资讯短视频")
    parser.add_argument("--count", type=int, default=int(os.environ.get("BATCH_VIDEO_COUNT", "10")))
    parser.add_argument("--days", type=int, default=int(os.environ.get("BATCH_SEARCH_DAYS", "7")))
    parser.add_argument("--source", choices=("feeds", "exa"), default=os.environ.get("AIVIDEO_SOURCE", "exa"))
    parser.add_argument("--fresh-hours", type=int, default=int(os.environ.get("AIVIDEO_FRESH_HOURS", "24")))
    parser.add_argument("--max-retries", type=int, default=int(os.environ.get("BATCH_MAX_RETRIES", "5")))
    parser.add_argument("--retry-pause", type=int, default=int(os.environ.get("BATCH_RETRY_PAUSE", "30")))
    parser.add_argument(
        "--sleep-between",
        type=int,
        default=int(os.environ.get("BATCH_SLEEP_BETWEEN", "60")),
        help="每条之间的间隔秒数",
    )
    parser.add_argument("--reset", action="store_true", help="清空进度，从头开始")
    args = parser.parse_args()

    if args.reset and PROGRESS_FILE.is_file():
        PROGRESS_FILE.unlink()
        log("已清空 batch 进度")

    progress = load_progress()
    progress["target"] = args.count
    progress["days"] = args.days
    save_progress(progress)

    completed_indices = {int(x["index"]) for x in progress.get("completed") or [] if x.get("index")}

    log("=== AIVideo 批量任务 ===")
    window = f"固定信息源近 {args.fresh_hours} 小时" if args.source == "feeds" else f"Exa 近 {args.days} 天"
    log(f"目标: {args.count} 条 | 候选: {window}")
    log(f"已完成: {len(completed_indices)}/{args.count}")
    log(f"进度文件: {PROGRESS_FILE}")
    log("发布: 主流程请使用 ./make-and-publish.sh")

    # 外层循环：直到全部成功
    while len(completed_indices) < args.count:
        for index in range(1, args.count + 1):
            if index in completed_indices:
                continue

            # 本批次内累积 + 历史窗口 URL 一并排除
            exclude = list(dict.fromkeys(exclude_urls(progress) + history_exclude_urls()))
            recent_topics = history_recent_topics()
            if recent_topics:
                log(f"  📚 近 {HISTORY_WINDOW_DAYS} 天已做过 {len(recent_topics)} 个主题，提醒 Opus 规避")
            try:
                item = process_one(
                    index,
                    days=args.days,
                    batch_total=args.count,
                    exclude=exclude,
                    max_retries=args.max_retries,
                    retry_pause=args.retry_pause,
                    recent_topics=recent_topics,
                    source=args.source,
                    fresh_hours=args.fresh_hours,
                )
            except RuntimeError as e:
                log(f"✗ 第 {index} 条失败，60s 后从断点继续: {e}")
                time.sleep(60)
                break
            except Exception as e:  # noqa: BLE001
                log(f"✗ 第 {index} 条异常，60s 后从断点继续: {e}")
                time.sleep(60)
                break

            progress.setdefault("completed", []).append(item)
            save_progress(progress)
            append_history(item)
            completed_indices.add(index)
            log(f"★ 进度 {len(completed_indices)}/{args.count} 完成")

            if len(completed_indices) < args.count and index < args.count:
                log(f"⏸  等待 {args.sleep_between}s 再制作下一条…")
                time.sleep(args.sleep_between)
        else:
            continue
        # inner break → retry outer while

    log(f"=== 全部完成：{args.count} 条视频已制作 ===")
    log("发布抖音: ./make-and-publish.sh 会自动处理")
    for item in sorted(progress["completed"], key=lambda x: x["index"]):
        log(f"  [{item['index']:02d}] {item.get('title')} → {item.get('video')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
