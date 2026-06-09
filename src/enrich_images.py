#!/usr/bin/env python3
"""为脚本 JSON 的每页 slide 调用 AiHubMix 生图，写入本地 image_path。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from image_client import build_cover_prompt, build_prompt, generate_image, save_b64_image
from locale_env import locale_logs_dir
from paths import ROOT
from research import load_env


def image_dir_for(script_path: Path) -> Path:
    return locale_logs_dir() / "images" / script_path.stem


def slide_fingerprint(slide: dict) -> str:
    payload = json.dumps(
        {
            "image_prompt": slide.get("image_prompt") or "",
            "on_image_text": slide.get("on_image_text") or [],
            "headline": slide.get("headline") or "",
            "chapter_title": slide.get("chapter_title") or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def cover_fingerprint(title: str, subtitle: str, keyword: str) -> str:
    payload = json.dumps(
        {"title": title, "subtitle": subtitle, "keyword": keyword},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _generate_cover(
    *,
    title: str,
    subtitle: str,
    keyword: str,
    out_path: Path,
    fp: str,
) -> dict:
    prompt = build_cover_prompt(title=title, subtitle=subtitle, keyword=keyword)
    print(f"  [cover] 生图中… {prompt[:90]}…", file=sys.stderr)
    result = generate_image(prompt)
    if result.get("b64_json"):
        save_b64_image(result["b64_json"], out_path)
    elif result.get("url"):
        _download_url(str(result["url"]), out_path)
    else:
        raise RuntimeError("封面无图片数据")
    print(f"  [cover] ✓ {result.get('elapsed_s')}s → {out_path}", file=sys.stderr)
    return {
        "index": 0,
        "image_path": str(out_path.relative_to(ROOT)),
        "elapsed_s": result.get("elapsed_s"),
        "fingerprint": fp,
        "is_cover": True,
    }


def should_skip_slide(
    slide: dict,
    *,
    force: bool,
    local_png: Path,
    prev_fingerprint: str | None,
    cur_fingerprint: str,
) -> bool:
    if force:
        return False
    if not (local_png.is_file() and local_png.stat().st_size > 1024):
        return False
    return prev_fingerprint == cur_fingerprint


def _generate_one(
    *,
    idx: int,
    total: int,
    slide: dict,
    out_dir: Path,
    fingerprint: str,
) -> dict:
    """单页生图（线程内执行）。返回 meta 条目，并直接写 slide['image_path']。"""
    local_path = out_dir / f"slide_{idx:02d}.png"
    prompt = build_prompt(
        str(slide.get("image_prompt") or ""),
        headline=str(slide.get("headline") or ""),
        chapter_title=str(slide.get("chapter_title") or ""),
        on_image_text=slide.get("on_image_text") or [],
        page_index=idx,
        total_pages=total,
    )
    print(f"  [{idx}/{total}] 生图中… prompt: {prompt[:80]}…", file=sys.stderr)
    result = generate_image(prompt)
    if result.get("b64_json"):
        save_b64_image(result["b64_json"], local_path)
    elif result.get("url"):
        _download_url(str(result["url"]), local_path)
    else:
        raise RuntimeError(f"第 {idx} 页无图片数据")

    rel_path = str(local_path.relative_to(ROOT))
    slide["image_path"] = rel_path
    slide.pop("image_url", None)
    slide.pop("image_b64", None)
    print(f"  [{idx}/{total}] ✓ {result.get('elapsed_s')}s → {rel_path}", file=sys.stderr)
    return {
        "index": idx,
        "image_path": rel_path,
        "elapsed_s": result.get("elapsed_s"),
        "revised_prompt": result.get("revised_prompt"),
        "fingerprint": fingerprint,
    }


def enrich_script_file(
    script_path: Path,
    *,
    force: bool = False,
    max_workers: int | None = None,
) -> dict:
    data = json.loads(script_path.read_text(encoding="utf-8"))
    script = data.get("script", data)
    slides = script.get("slides")
    if not isinstance(slides, list):
        raise ValueError("脚本缺少 slides 数组")

    out_dir = image_dir_for(script_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = data.setdefault("meta", {})
    prev_image_meta = meta.get("images") or {}
    prev_fingerprints: dict[int, str] = {}
    for entry in prev_image_meta.get("slides") or []:
        if isinstance(entry, dict) and entry.get("fingerprint"):
            prev_fingerprints[int(entry.get("index") or 0)] = str(entry["fingerprint"])

    image_meta = meta["images"] = {}
    image_meta["provider"] = "aihubmix"
    image_meta["model"] = os.environ.get("AIHUBMIX_IMAGE_MODEL", "gpt-image-2")
    image_meta["generated_at"] = datetime.now(timezone.utc).isoformat()
    image_meta["slides"] = []

    total = len(slides)
    tasks: list[tuple[int, dict, str]] = []

    # 封面任务（slide_00 / cover.png）
    title = str(script.get("title") or "").strip()
    cover_slide = slides[0] if slides else {}
    subtitle = str(cover_slide.get("subtitle") or "").strip()
    keyword = str(script.get("keyword") or "").strip()
    cover_path = out_dir / "cover.png"
    cover_fp = cover_fingerprint(title, subtitle, keyword)
    prev_cover_fp: str | None = None
    for entry in prev_image_meta.get("slides") or []:
        if isinstance(entry, dict) and entry.get("is_cover"):
            prev_cover_fp = str(entry.get("fingerprint") or "") or None
            break
    # 全屏 AI 封面海报：默认开启（是必要的视觉钩子/封面）。控成本靠减少正文页（AIVIDEO_MAX_SLIDES）。
    ai_cover_enabled = os.environ.get("AIVIDEO_AI_COVER", "1").lower() in ("1", "true", "yes", "on")
    cover_task_needed = ai_cover_enabled and bool(title) and (
        force
        or not (cover_path.is_file() and cover_path.stat().st_size > 1024)
        or prev_cover_fp != cover_fp
    )
    cover_meta_entry: dict | None = None
    if title and ai_cover_enabled and not cover_task_needed:
        cover_meta_entry = {
            "index": 0,
            "image_path": str(cover_path.relative_to(ROOT)),
            "skipped": True,
            "fingerprint": cover_fp,
            "is_cover": True,
        }
        print("  [cover] 跳过（title 未变）", file=sys.stderr)
    elif title:
        # 占位：稍后用线程池执行
        pass

    for i, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            raise ValueError(f"第 {i} 页 slide 不是对象")

        local_path = out_dir / f"slide_{i:02d}.png"
        fingerprint = slide_fingerprint(slide)
        if should_skip_slide(
            slide,
            force=force,
            local_png=local_path,
            prev_fingerprint=prev_fingerprints.get(i),
            cur_fingerprint=fingerprint,
        ):
            slide["image_path"] = str(local_path.relative_to(ROOT))
            print(f"  [{i}/{total}] 跳过（prompt 未变）", file=sys.stderr)
            image_meta["slides"].append(
                {"index": i, "skipped": True, "image_path": slide["image_path"], "fingerprint": fingerprint}
            )
            continue
        tasks.append((i, slide, fingerprint))

    total_tasks = len(tasks) + (1 if cover_task_needed else 0)
    if total_tasks:
        workers = max_workers or int(os.environ.get("AIHUBMIX_PARALLEL", "5"))
        workers = max(1, min(workers, total_tasks))
        print(f"  并行生图：{total_tasks} 张（含封面 {int(cover_task_needed)}），{workers} 路并发", file=sys.stderr)
        write_lock = threading.Lock()
        results_by_idx: dict[int, dict] = {}
        errors: list[tuple[int, BaseException]] = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures: dict = {}
            for idx, slide, fp in tasks:
                futures[ex.submit(
                    _generate_one,
                    idx=idx, total=total, slide=slide,
                    out_dir=out_dir, fingerprint=fp,
                )] = idx
            if cover_task_needed:
                futures[ex.submit(
                    _generate_cover,
                    title=title, subtitle=subtitle, keyword=keyword,
                    out_path=cover_path, fp=cover_fp,
                )] = 0  # cover 标记为 index 0
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    entry = fut.result()
                    results_by_idx[idx] = entry
                except BaseException as exc:  # noqa: BLE001
                    errors.append((idx, exc))
                    tag = "封面" if idx == 0 else f"第 {idx} 页"
                    print(f"  ✗ {tag}生图失败: {exc}", file=sys.stderr)
                    continue
                with write_lock:
                    script_path.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                    )

        if 0 in results_by_idx:
            cover_meta_entry = results_by_idx[0]
        for idx, _, _ in tasks:
            if idx in results_by_idx:
                image_meta["slides"].append(results_by_idx[idx])

        if errors:
            script_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            raise RuntimeError(
                "部分页生图失败: " + ", ".join(
                    ("封面" if i == 0 else f"#{i}") + f"({type(e).__name__})" for i, e in errors
                )
            )

    if cover_meta_entry is not None:
        image_meta["slides"].append(cover_meta_entry)
        script["cover_image"] = cover_meta_entry["image_path"]

    image_meta["slides"].sort(key=lambda x: x.get("index", 0))
    script_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return script


def _download_url(url: str, path: Path) -> None:
    import urllib.request
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as resp:
        path.write_bytes(resp.read())


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="为脚本 slides 生成配图（AiHubMix）")
    parser.add_argument("script_file", nargs="?", default=str(ROOT / "logs" / "last_script.json"))
    parser.add_argument("--force", action="store_true", help="忽略已有图，重新生图")
    parser.add_argument(
        "--workers", type=int, default=None,
        help="并发数；默认读 AIHUBMIX_PARALLEL，再 fallback 到 5",
    )
    args = parser.parse_args()

    script_path = Path(args.script_file)
    if not script_path.is_file():
        print(f"脚本不存在: {script_path}", file=sys.stderr)
        return 1

    print(f"生图: {script_path}", file=sys.stderr)
    try:
        enrich_script_file(script_path, force=args.force, max_workers=args.workers)
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(f"生图失败: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc()
        return 1

    print("完成。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
