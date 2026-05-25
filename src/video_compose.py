#!/usr/bin/env python3
"""本地视频合成：底图 (78%) + 字幕 + 5 圆点进度条；ffmpeg 合成单段 → concat。

用法:
    python3 src/video_compose.py logs/last_script.json -o output/xxx.mp4
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from paths import ROOT
from research import load_env
from tts_client import synthesize as tts_synthesize

CANVAS_W = 1080
CANVAS_H = 1920
IMAGE_AREA_H = 1500          # top 78%
BOTTOM_AREA_H = CANVAS_H - IMAGE_AREA_H  # 420
BG_COLOR = (251, 246, 228)   # 暖米色，匹配方格纸
TEXT_COLOR = (40, 40, 40)
DOT_FILL = (34, 34, 34)
DOT_STROKE = (160, 160, 160)
LINE_COLOR = (210, 210, 210)
SUBTITLE_BG = (0, 0, 0, 140)
SUBTITLE_FG = (255, 255, 255)


def font_path() -> str:
    p = os.environ.get("AIVIDEO_FONT", "assets/HiraginoSansGB.ttc").strip()
    fp = Path(p) if Path(p).is_absolute() else ROOT / p
    if not fp.is_file():
        raise RuntimeError(f"字体不存在: {fp}（设置 AIVIDEO_FONT）")
    return str(fp)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path(), size=size)


def render_base_canvas(
    image_path: Path,
    *,
    chapter_title: str,
    page_index: int,
    total_pages: int,
    out_path: Path,
) -> Path:
    """渲染单页静态底图 (1080x1920)：图 + 进度条（不含字幕）。"""
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), BG_COLOR)

    if image_path.is_file():
        img = Image.open(image_path).convert("RGB")
        ratio = min(CANVAS_W / img.width, IMAGE_AREA_H / img.height)
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        x = (CANVAS_W - new_w) // 2
        y = (IMAGE_AREA_H - new_h) // 2
        canvas.paste(img, (x, y))

    draw = ImageDraw.Draw(canvas)

    dot_r = 14
    spacing = 80
    total = max(1, total_pages)
    cy = IMAGE_AREA_H + 320  # y=1820
    total_w = (total - 1) * spacing
    cx0 = (CANVAS_W - total_w) // 2

    for i in range(total - 1):
        x1 = cx0 + i * spacing + dot_r + 2
        x2 = cx0 + (i + 1) * spacing - dot_r - 2
        draw.line([(x1, cy), (x2, cy)], fill=LINE_COLOR, width=2)

    for i in range(total):
        cx = cx0 + i * spacing
        bbox = (cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r)
        if i + 1 <= page_index:
            draw.ellipse(bbox, fill=DOT_FILL)
        else:
            draw.ellipse(bbox, outline=DOT_STROKE, width=2)

    if chapter_title:
        font = load_font(28)
        bbox = font.getbbox(chapter_title)
        tw = bbox[2] - bbox[0]
        cx = cx0 + (page_index - 1) * spacing
        draw.text((cx - tw // 2, cy + 24), chapter_title, font=font, fill=TEXT_COLOR)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


_PHRASE_SPLIT = re.compile(r"[，。！？；,!?;\n]+")


def split_narration(text: str, max_chars: int = 18) -> list[str]:
    """按标点切句；过长再按字数切。"""
    text = (text or "").strip()
    if not text:
        return []
    raw = [p.strip() for p in _PHRASE_SPLIT.split(text) if p.strip()]
    out: list[str] = []
    for phrase in raw:
        while len(phrase) > max_chars:
            cut = max_chars
            for i in range(max_chars - 4, max_chars):
                if i < len(phrase) and phrase[i] in " 、的了":
                    cut = i + 1
                    break
            out.append(phrase[:cut])
            phrase = phrase[cut:].lstrip()
        if phrase:
            out.append(phrase)
    return out


def allocate_phrase_times(phrases: list[str], total_duration: float) -> list[tuple[float, float]]:
    """按字符数比例分配时长，返回 (start, end) 列表。"""
    if not phrases:
        return []
    weights = [max(1, len(p)) for p in phrases]
    total_w = sum(weights)
    spans: list[tuple[float, float]] = []
    t = 0.0
    for i, w in enumerate(weights):
        if i == len(weights) - 1:
            spans.append((t, total_duration))
        else:
            d = total_duration * (w / total_w)
            spans.append((t, t + d))
            t += d
    return spans


def ffprobe_duration(path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]).decode().strip()
    return float(out)


_DRAWTEXT_ESCAPE = str.maketrans({"\\": r"\\", ":": r"\:", "'": r"\'", "%": r"\%"})


def _escape_drawtext_path(p: str) -> str:
    return p.translate(_DRAWTEXT_ESCAPE)


def _make_phrase_textfile(phrase: str, out: Path) -> Path:
    out.write_text(phrase, encoding="utf-8")
    return out


def _drawtext_filter(
    *,
    textfile: Path,
    font: str,
    fontsize: int,
    y: int,
    start: float,
    end: float,
) -> str:
    parts = [
        f"fontfile={_escape_drawtext_path(font)}",
        f"textfile={_escape_drawtext_path(str(textfile))}",
        "fontcolor=white",
        f"fontsize={fontsize}",
        "borderw=4",
        "bordercolor=black",
        "box=1",
        "boxcolor=black@0.55",
        "boxborderw=24",
        "x=(w-text_w)/2",
        f"y={y}",
        "line_spacing=10",
        f"enable='between(t,{start:.3f},{end:.3f})'",
    ]
    return "drawtext=" + ":".join(parts)


def compose_clip(
    *,
    base_image: Path,
    audio_path: Path,
    narration: str,
    out_path: Path,
    work_dir: Path,
) -> Path:
    duration = ffprobe_duration(audio_path)
    phrases = split_narration(narration) or [narration[:18]]
    spans = allocate_phrase_times(phrases, duration)

    work_dir.mkdir(parents=True, exist_ok=True)
    font = font_path()

    filters: list[str] = []
    for idx, (phrase, (start, end)) in enumerate(zip(phrases, spans)):
        tf = _make_phrase_textfile(phrase, work_dir / f"phrase_{idx:02d}.txt")
        filters.append(
            _drawtext_filter(
                textfile=tf,
                font=font,
                fontsize=54,
                y=1620,
                start=start,
                end=end,
            )
        )
    filter_chain = ",".join(filters) if filters else "null"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(base_image),
        "-i", str(audio_path),
        "-vf", filter_chain,
        "-r", "30",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-shortest",
        "-t", f"{duration:.3f}",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg compose 失败:\n{proc.stderr[-1500:]}")
    return out_path


def concat_clips(clips: list[Path], out_path: Path, work_dir: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    list_file = work_dir / "concat.txt"
    list_file.write_text(
        "\n".join(f"file '{c.resolve()}'" for c in clips) + "\n",
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # 拷贝流可能失败（不同时长基准），回退重新编码
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            str(out_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat 失败:\n{proc.stderr[-1500:]}")
    return out_path


def compose_video(
    script_file: Path,
    *,
    output: Path | None = None,
    work_dir: Path | None = None,
    skip_tts: bool = False,
) -> Path:
    data = json.loads(script_file.read_text(encoding="utf-8"))
    script = data.get("script", data)
    slides = script.get("slides") or []
    if not slides:
        raise ValueError("脚本无 slides")

    work_dir = (work_dir or ROOT / "logs" / "compose" / script_file.stem)
    work_dir.mkdir(parents=True, exist_ok=True)

    total = len(slides)
    clips: list[Path] = []
    for i, slide in enumerate(slides, start=1):
        print(f"[{i}/{total}] 合成单段：{slide.get('chapter_title') or slide.get('headline') or ''}", file=sys.stderr)

        image_rel = slide.get("image_path")
        if image_rel:
            image_path = Path(image_rel) if Path(image_rel).is_absolute() else ROOT / image_rel
        else:
            image_path = ROOT / "logs" / "images" / script_file.stem / f"slide_{i:02d}.png"
        if not image_path.is_file():
            print(f"  ⚠️  缺图: {image_path}", file=sys.stderr)
            image_path = work_dir / f"missing_{i}.png"
            Image.new("RGB", (CANVAS_W, IMAGE_AREA_H), BG_COLOR).save(image_path)

        base_png = work_dir / f"base_{i:02d}.png"
        render_base_canvas(
            image_path,
            chapter_title=str(slide.get("chapter_title") or ""),
            page_index=i,
            total_pages=total,
            out_path=base_png,
        )

        narration = str(slide.get("narration") or "")
        audio_path = work_dir / f"audio_{i:02d}.mp3"
        if not skip_tts or not audio_path.is_file():
            print(f"   TTS …", file=sys.stderr)
            tts_synthesize(narration, out_path=audio_path)

        clip_path = work_dir / f"clip_{i:02d}.mp4"
        print("   ffmpeg 合成 …", file=sys.stderr)
        compose_clip(
            base_image=base_png,
            audio_path=audio_path,
            narration=narration,
            out_path=clip_path,
            work_dir=work_dir / f"phrases_{i:02d}",
        )
        clips.append(clip_path)

    output = output or (ROOT / "output" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"[concat] {len(clips)} 段 → {output}", file=sys.stderr)
    concat_clips(clips, output, work_dir)
    print(f"完成：{output} ({output.stat().st_size//1024} KB)", file=sys.stderr)

    last_video = ROOT / "logs" / "last_video.txt"
    last_video.write_text(str(output) + "\n", encoding="utf-8")
    manifest = ROOT / "logs" / "video_manifest.jsonl"
    with manifest.open("a", encoding="utf-8") as mf:
        mf.write(json.dumps({
            "video": str(output),
            "script": str(script_file),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "compose": "local",
        }, ensure_ascii=False) + "\n")
    return output


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="本地合成视频（图片+TTS+ffmpeg）")
    parser.add_argument("script_file", nargs="?", default=str(ROOT / "logs" / "last_script.json"))
    parser.add_argument("-o", "--output")
    parser.add_argument("--skip-tts", action="store_true", help="复用已有音频")
    args = parser.parse_args()

    script_path = Path(args.script_file)
    if not script_path.is_file():
        print(f"脚本不存在: {script_path}", file=sys.stderr)
        return 1
    out = Path(args.output) if args.output else None
    compose_video(script_path, output=out, skip_tts=args.skip_tts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
