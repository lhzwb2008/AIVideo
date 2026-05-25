#!/usr/bin/env python3
"""为脚本 JSON 的每页 slide 调用 AiHubMix 生图，写入本地 image_path。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from image_client import build_prompt, generate_image, save_b64_image
from paths import ROOT
from research import load_env


def image_dir_for(script_path: Path) -> Path:
    return ROOT / "logs" / "images" / script_path.stem


def should_skip_slide(slide: dict, *, force: bool, local_png: Path) -> bool:
    if force:
        return False
    return local_png.is_file() and local_png.stat().st_size > 1024


def enrich_script_file(script_path: Path, *, force: bool = False) -> dict:
    data = json.loads(script_path.read_text(encoding="utf-8"))
    script = data.get("script", data)
    slides = script.get("slides")
    if not isinstance(slides, list):
        raise ValueError("脚本缺少 slides 数组")

    out_dir = image_dir_for(script_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = data.setdefault("meta", {})
    image_meta = meta.setdefault("images", {})
    image_meta["provider"] = "aihubmix"
    import os
    image_meta["model"] = os.environ.get("AIHUBMIX_IMAGE_MODEL", "gpt-image-2")
    image_meta["generated_at"] = datetime.now(timezone.utc).isoformat()
    image_meta["slides"] = []

    total = len(slides)
    for i, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            raise ValueError(f"第 {i} 页 slide 不是对象")

        local_path = out_dir / f"slide_{i:02d}.png"
        if should_skip_slide(slide, force=force, local_png=local_path):
            slide["image_path"] = str(local_path.relative_to(ROOT))
            print(f"  [{i}/{total}] 跳过（已有图）", file=sys.stderr)
            image_meta["slides"].append({"index": i, "skipped": True, "image_path": slide["image_path"]})
            continue

        prompt = build_prompt(
            str(slide.get("image_prompt") or ""),
            headline=str(slide.get("headline") or ""),
            chapter_title=str(slide.get("chapter_title") or ""),
            on_image_text=slide.get("on_image_text") or [],
            page_index=i,
            total_pages=total,
        )
        print(f"  [{i}/{total}] 生图中…", file=sys.stderr)
        print(f"    prompt: {prompt[:120]}…", file=sys.stderr)

        try:
            result = generate_image(prompt)
        except RuntimeError as exc:
            print(f"  ✗ 第 {i} 页生图失败: {exc}", file=sys.stderr)
            script_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            raise

        if result.get("b64_json"):
            save_b64_image(result["b64_json"], local_path)
        elif result.get("url"):
            _download_url(str(result["url"]), local_path)
        else:
            raise RuntimeError(f"第 {i} 页无图片数据")

        rel_path = str(local_path.relative_to(ROOT))
        slide["image_path"] = rel_path
        # 清理旧字段
        slide.pop("image_url", None)
        slide.pop("image_b64", None)

        print(f"    ✓ {result.get('elapsed_s')}s → {rel_path}", file=sys.stderr)
        image_meta["slides"].append(
            {
                "index": i,
                "image_path": rel_path,
                "elapsed_s": result.get("elapsed_s"),
                "revised_prompt": result.get("revised_prompt"),
            }
        )
        # 每页完成立即写盘，断点续跑
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
    args = parser.parse_args()

    script_path = Path(args.script_file)
    if not script_path.is_file():
        print(f"脚本不存在: {script_path}", file=sys.stderr)
        return 1

    print(f"生图: {script_path}", file=sys.stderr)
    try:
        enrich_script_file(script_path, force=args.force)
    except Exception as exc:  # noqa: BLE001
        import traceback
        print(f"生图失败: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        traceback.print_exc()
        return 1

    print("完成。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
