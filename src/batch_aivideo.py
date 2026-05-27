#!/usr/bin/env python3
"""旧批量制作入口。主流程请使用 ./make-and-publish.sh。"""

from __future__ import annotations

import argparse
import json
import os
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
HISTORY_WINDOW_DAYS = int(os.environ.get("BATCH_HISTORY_DAYS", "21"))


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
    append_history({
        "url": article.get("url") or (script.get("source") or {}).get("url") or "",
        "title": article.get("title") or script.get("title") or "",
    })


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
