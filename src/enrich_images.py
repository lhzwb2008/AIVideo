#!/usr/bin/env python3
"""为脚本 JSON 的每页 slide 调用 AiHubMix 生图，写入 image_url / image_path。"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from image_client import build_prompt, generate_image, save_b64_image, upload_public
from paths import ROOT
from research import load_env


def image_dir_for(script_path: Path) -> Path:
    stem = script_path.stem
    return ROOT / "logs" / "images" / stem


def should_skip_slide(slide: dict, *, force: bool) -> bool:
    if force:
        return False
    return bool(slide.get("image_url"))


def enrich_script_file(
    script_path: Path,
    *,
    force: bool = False,
    upload: bool | None = None,
    include_b64: bool | None = None,
) -> dict:
    data = json.loads(script_path.read_text(encoding="utf-8"))
    script = data.get("script", data)
    slides = script.get("slides")
    if not isinstance(slides, list):
        raise ValueError("脚本缺少 slides 数组")

    upload_mode = os.environ.get("AIVIDEO_IMAGE_UPLOAD", "catbox").strip().lower()
    do_upload = upload if upload is not None else upload_mode not in ("0", "none", "off", "skip")
    do_b64 = include_b64 if include_b64 is not None else _env_bool("AIVIDEO_IMAGE_INCLUDE_B64", "0")

    out_dir = image_dir_for(script_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = data.setdefault("meta", {})
    image_meta = meta.setdefault("images", {})
    image_meta["provider"] = "aihubmix"
    image_meta["model"] = os.environ.get("AIHUBMIX_IMAGE_MODEL", "gpt-image-2")
    image_meta["generated_at"] = datetime.now(timezone.utc).isoformat()
    image_meta["slides"] = []

    total = len(slides)
    for i, slide in enumerate(slides, start=1):
        if not isinstance(slide, dict):
            raise ValueError(f"第 {i} 页 slide 不是对象")

        if should_skip_slide(slide, force=force):
            print(f"  [{i}/{total}] 跳过（已有 image_url）", file=sys.stderr)
            image_meta["slides"].append({"index": i, "skipped": True, "image_url": slide.get("image_url")})
            continue

        prompt = build_prompt(
            str(slide.get("image_prompt") or ""),
            headline=str(slide.get("headline") or ""),
        )
        print(f"  [{i}/{total}] 生图中…", file=sys.stderr)
        print(f"    prompt: {prompt[:120]}…", file=sys.stderr)

        result = generate_image(prompt)
        local_path = out_dir / f"slide_{i:02d}.png"
        if result.get("b64_json"):
            save_b64_image(result["b64_json"], local_path)
        elif result.get("url"):
            _download_url(str(result["url"]), local_path)
        else:
            raise RuntimeError(f"第 {i} 页无图片数据")

        rel_path = str(local_path.relative_to(ROOT))
        slide["image_path"] = rel_path
        slide.pop("image_b64", None)

        public_url = result.get("url")
        if not public_url and do_upload:
            public_url = upload_public(local_path)
        if public_url:
            slide["image_url"] = public_url
        elif do_b64:
            import base64

            b64 = result.get("b64_json")
            if not b64:
                b64 = base64.b64encode(local_path.read_bytes()).decode("ascii")
            slide["image_b64"] = b64

        if not slide.get("image_url") and not slide.get("image_b64"):
            raise RuntimeError(
                f"第 {i} 页无法提供 Coze 可用图片：请开启 AIVIDEO_IMAGE_UPLOAD=catbox "
                "或 AIVIDEO_IMAGE_INCLUDE_B64=1"
            )

        print(
            f"    ✓ {result.get('elapsed_s')}s → {rel_path}"
            + (f" | {public_url}" if slide.get("image_url") else " | b64"),
            file=sys.stderr,
        )
        image_meta["slides"].append(
            {
                "index": i,
                "image_path": rel_path,
                "image_url": slide.get("image_url"),
                "elapsed_s": result.get("elapsed_s"),
                "revised_prompt": result.get("revised_prompt"),
            }
        )

    script_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return script


def _download_url(url: str, path: Path) -> None:
    import urllib.request

    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as resp:
        path.write_bytes(resp.read())


def _env_bool(name: str, default: str) -> bool:
    val = os.environ.get(name, default).strip().lower()
    return val not in ("0", "false", "no", "off", "")


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="为脚本 slides 生成配图（AiHubMix）")
    parser.add_argument("script_file", nargs="?", default=str(ROOT / "logs" / "last_script.json"))
    parser.add_argument("--force", action="store_true", help="忽略已有 image_url，重新生图")
    parser.add_argument("--no-upload", action="store_true", help="不上传公网，仅写本地 image_path")
    parser.add_argument("--include-b64", action="store_true", help="写入 image_b64（JSON 较大）")
    args = parser.parse_args()

    script_path = Path(args.script_file)
    if not script_path.is_file():
        print(f"脚本不存在: {script_path}", file=sys.stderr)
        return 1

    print(f"生图: {script_path}", file=sys.stderr)
    try:
        enrich_script_file(
            script_path,
            force=args.force,
            upload=False if args.no_upload else None,
            include_b64=True if args.include_b64 else None,
        )
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1

    print("完成。Coze 工作流请优先读取 slide.image_url，跳过内部生图。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
