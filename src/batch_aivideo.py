#!/usr/bin/env python3
"""批量：近 N 天 AI 新闻 → 多条视频。支持断点续跑、逐步重试直到成功。发布请用 ./publish-all-douyin.sh"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from paths import ROOT
from research import load_env, run_research

DEFAULT_TOPIC = "近1个月AI新闻"
PROGRESS_FILE = ROOT / "logs" / "batch_progress.json"
BATCH_LOG = ROOT / "logs" / "batch_run.log"


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
        "days": 30,
        "topic": DEFAULT_TOPIC,
        "completed": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }


def save_progress(data: dict) -> None:
    PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    PROGRESS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def exclude_keywords(progress: dict) -> list[str]:
    keys: list[str] = []
    for item in progress.get("completed") or []:
        kw = str(item.get("keyword") or "").strip()
        if kw:
            keys.append(kw)
    return keys


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


def run_coze(script_path: Path) -> Path:
    env = os.environ.copy()
    proc = subprocess.run(
        [str(ROOT / "run-coze.sh"), str(script_path)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "Coze 合成失败")

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
    topic: str,
    days: int,
    batch_total: int,
    exclude: list[str],
    max_retries: int,
    retry_pause: int,
) -> dict:
    batch_dir = ROOT / "logs" / "batch"
    batch_dir.mkdir(parents=True, exist_ok=True)
    script_path = batch_dir / f"{index:02d}_script.json"

    log(f"━━━ [{index}/{batch_total}] 调研 ━━━")
    agent_id: str | None = None
    if (ROOT / "logs" / "cursor_agent.json").is_file():
        try:
            agent_id = json.loads((ROOT / "logs" / "cursor_agent.json").read_text())["agent_id"]
        except (json.JSONDecodeError, KeyError):
            agent_id = None

    def do_research() -> dict:
        nonlocal agent_id
        script, agent_id = run_research(
            topic,
            output=script_path,
            agent_id=agent_id,
            days=days,
            exclude_keywords=exclude or None,
            batch_index=index,
            batch_total=batch_total,
        )
        return script

    script = retry("调研", do_research, max_attempts=max_retries, pause=retry_pause)
    log(f"  ✓ 脚本: {script.get('title')} (keyword={script.get('keyword')})")

    if os.environ.get("AIHUBMIX_API_KEY", "").strip():
        log(f"━━━ [{index}/{batch_total}] API 生图 ━━━")

        def do_enrich() -> None:
            proc = subprocess.run(
                [str(ROOT / "run-enrich-images.sh"), str(script_path)],
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

    log(f"━━━ [{index}/{batch_total}] Coze 合成 ━━━")
    video = retry(
        "Coze 合成",
        lambda: run_coze(script_path),
        max_attempts=max_retries,
        pause=retry_pause,
    )
    log(f"  ✓ 视频: {rel_path(video)}")

    return {
        "index": index,
        "keyword": script.get("keyword"),
        "title": script.get("title"),
        "script": rel_path(script_path),
        "video": rel_path(video),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="批量制作并发布 AI 资讯短视频")
    parser.add_argument("--count", type=int, default=int(os.environ.get("BATCH_VIDEO_COUNT", "10")))
    parser.add_argument("--days", type=int, default=int(os.environ.get("BATCH_SEARCH_DAYS", "30")))
    parser.add_argument("--topic", default=os.environ.get("BATCH_TOPIC", DEFAULT_TOPIC))
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
    progress["topic"] = args.topic
    save_progress(progress)

    completed_indices = {int(x["index"]) for x in progress.get("completed") or [] if x.get("index")}

    log("=== AIVideo 批量任务 ===")
    log(f"目标: {args.count} 条 | 时间窗: 近 {args.days} 天 | 话题: {args.topic}")
    log(f"已完成: {len(completed_indices)}/{args.count}")
    log(f"进度文件: {PROGRESS_FILE}")
    log("发布: 制作完成后运行 ./publish-all-douyin.sh")

    # 外层循环：直到全部成功
    while len(completed_indices) < args.count:
        for index in range(1, args.count + 1):
            if index in completed_indices:
                continue

            exclude = exclude_keywords(progress)
            try:
                item = process_one(
                    index,
                    topic=args.topic,
                    days=args.days,
                    batch_total=args.count,
                    exclude=exclude,
                    max_retries=args.max_retries,
                    retry_pause=args.retry_pause,
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
            completed_indices.add(index)
            log(f"★ 进度 {len(completed_indices)}/{args.count} 完成")

            if len(completed_indices) < args.count and index < args.count:
                log(f"⏸  等待 {args.sleep_between}s 再制作下一条…")
                time.sleep(args.sleep_between)
        else:
            continue
        # inner break → retry outer while

    log(f"=== 全部完成：{args.count} 条视频已制作 ===")
    log("发布抖音: ./publish-all-douyin.sh")
    for item in sorted(progress["completed"], key=lambda x: x["index"]):
        log(f"  [{item['index']:02d}] {item.get('title')} → {item.get('video')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
