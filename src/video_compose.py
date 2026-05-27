#!/usr/bin/env python3
"""本地视频合成：底图 (78%) + 字幕 + 5 圆点进度条；ffmpeg 合成单段 → concat。

用法:
    python3 src/video_compose.py logs/last_script.json -o output/xxx.mp4
"""

from __future__ import annotations

import argparse
import hashlib
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
IMAGE_AREA_H = 1500            # 图片占满顶部 78%
BG_COLOR = (251, 246, 228)     # 暖米色，匹配方格纸
TEXT_COLOR = (40, 40, 40)
SUBTITLE_Y = 1640              # 字幕在图片下方留白区

COVER_DURATION_S = 2.6
TTS_SAMPLE_RATE = 24000        # 与 DASHSCOPE_TTS_SAMPLE_RATE 保持一致

# ============================================================
# 栏目品牌：AI财知道
# ============================================================
BRAND_NAME = os.environ.get("AIVIDEO_BRAND_NAME", "AI财知道").strip()
BRAND_TAGLINE = os.environ.get("AIVIDEO_BRAND_TAGLINE", "每天一个 AI 财经为什么").strip()
OUTRO_NARRATION = os.environ.get(
    "AIVIDEO_OUTRO_NARRATION",
    "我是AI财知道，每天用大白话讲清一个AI和财经热点。觉得有用就点个关注加点赞，下条更新别错过！",
).strip()
OUTRO_HEADLINE = os.environ.get("AIVIDEO_OUTRO_HEADLINE", "点赞 · 收藏 · 关注").strip()
OUTRO_SUBLINE = os.environ.get("AIVIDEO_OUTRO_SUBLINE", "一起看懂 AI 和钱的事").strip()


def font_path() -> str:
    p = os.environ.get("AIVIDEO_FONT", "assets/HiraginoSansGB.ttc").strip()
    fp = Path(p) if Path(p).is_absolute() else ROOT / p
    if not fp.is_file():
        raise RuntimeError(f"字体不存在: {fp}（设置 AIVIDEO_FONT）")
    return str(fp)


def load_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path(), size=size)


def _fit_font_size(text: str, max_width: int, base_size: int, min_size: int = 18) -> int:
    """二分缩字体让文本宽度不超过 max_width。"""
    size = base_size
    while size > min_size:
        font = load_font(size)
        if font.getbbox(text)[2] <= max_width:
            return size
        size -= 2
    return min_size


def _draw_brand_badge(
    draw: ImageDraw.ImageDraw,
    *,
    x: int = 36,
    y: int = 36,
    font_size: int = 46,
) -> None:
    """左上角小徽标：黄色 highlight + 黑字品牌名。栏目品牌透出。"""
    if not BRAND_NAME:
        return
    font = load_font(font_size)
    bbox = font.getbbox(BRAND_NAME)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x, pad_y = 22, 12
    box_x1, box_y1 = x, y
    box_x2 = x + text_w + pad_x * 2
    box_y2 = y + text_h + pad_y * 2
    shadow = 5
    draw.rounded_rectangle(
        [(box_x1 + shadow, box_y1 + shadow), (box_x2 + shadow, box_y2 + shadow)],
        radius=16, fill=(40, 40, 40),
    )
    draw.rounded_rectangle(
        [(box_x1, box_y1), (box_x2, box_y2)],
        radius=16, fill=(254, 224, 71), outline=(40, 40, 40), width=3,
    )
    draw.text((box_x1 + pad_x, box_y1 + pad_y - bbox[1]), BRAND_NAME, font=font, fill=(40, 40, 40))


def render_base_canvas(
    image_path: Path,
    *,
    out_path: Path,
    **_unused,
) -> Path:
    """单页静态底图 (1080x1920)：纯图 + 米色留白（字幕由 ffmpeg 叠加）。"""
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
    _draw_brand_badge(ImageDraw.Draw(canvas))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


def _wrap_chinese(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """按字符宽度逐字换行，避免溢出。"""
    lines: list[str] = []
    cur = ""
    for ch in text:
        candidate = cur + ch
        if font.getbbox(candidate)[2] <= max_w:
            cur = candidate
        else:
            if cur:
                lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines


def render_full_cover(image_path: Path, *, out_path: Path) -> Path:
    """AI 生成的封面图直接铺满 1080x1920，按比例缩放居中。"""
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), BG_COLOR)
    img = Image.open(image_path).convert("RGB")
    ratio = min(CANVAS_W / img.width, CANVAS_H / img.height)
    new_w = int(img.width * ratio)
    new_h = int(img.height * ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    x = (CANVAS_W - new_w) // 2
    y = (CANVAS_H - new_h) // 2
    canvas.paste(img, (x, y))
    _draw_brand_badge(ImageDraw.Draw(canvas), font_size=54)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


def _draw_grid(draw: ImageDraw.ImageDraw) -> None:
    grid = (232, 220, 188)
    for x in range(0, CANVAS_W, 60):
        draw.line([(x, 0), (x, CANVAS_H)], fill=grid, width=1)
    for y in range(0, CANVAS_H, 60):
        draw.line([(0, y), (CANVAS_W, y)], fill=grid, width=1)


def _draw_corner_doodles(draw: ImageDraw.ImageDraw) -> None:
    """四角随手画几笔，避免空白；纯黑钢笔线条。"""
    ink = (40, 40, 40)
    draw.line([(70, 110), (160, 110)], fill=ink, width=6)
    draw.line([(70, 110), (70, 200)], fill=ink, width=6)
    draw.line([(CANVAS_W - 160, 110), (CANVAS_W - 70, 110)], fill=ink, width=6)
    draw.line([(CANVAS_W - 70, 110), (CANVAS_W - 70, 200)], fill=ink, width=6)
    draw.line([(70, CANVAS_H - 200), (70, CANVAS_H - 110)], fill=ink, width=6)
    draw.line([(70, CANVAS_H - 110), (160, CANVAS_H - 110)], fill=ink, width=6)
    draw.line([(CANVAS_W - 70, CANVAS_H - 200), (CANVAS_W - 70, CANVAS_H - 110)], fill=ink, width=6)
    draw.line([(CANVAS_W - 160, CANVAS_H - 110), (CANVAS_W - 70, CANVAS_H - 110)], fill=ink, width=6)
    draw.ellipse([(CANVAS_W - 220, 60), (CANVAS_W - 170, 110)], outline=ink, width=5)
    draw.line([(CANVAS_W - 195, 110), (CANVAS_W - 195, 150)], fill=ink, width=5)
    draw.polygon(
        [(CANVAS_W - 215, 145), (CANVAS_W - 175, 145), (CANVAS_W - 195, 175)],
        fill=ink,
    )


def render_title_cover(
    *,
    title: str,
    subtitle: str,
    out_path: Path,
    hero_image: Path | None = None,
    **_unused,
) -> Path:
    """开场封面 (1080x1920)：上方手绘示意图 + 下方黄色标题块 + 副标，避免大片空白。"""
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), BG_COLOR)
    draw = ImageDraw.Draw(canvas)
    _draw_grid(draw)

    hero_area_h = 1180
    hero_top = 120
    if hero_image and hero_image.is_file():
        img = Image.open(hero_image).convert("RGB")
        max_w = CANVAS_W - 160
        ratio = min(max_w / img.width, hero_area_h / img.height)
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        x = (CANVAS_W - new_w) // 2
        y = hero_top + (hero_area_h - new_h) // 2
        canvas.paste(img, (x, y))
        draw.rectangle(
            [(x - 6, y - 6), (x + new_w + 6, y + new_h + 6)],
            outline=(40, 40, 40),
            width=4,
        )
    else:
        _draw_corner_doodles(draw)

    text_max_w = CANVAS_W - 220
    title_size = _fit_font_size(title, text_max_w, base_size=140, min_size=72)
    title_font = load_font(title_size)
    title_lines = _wrap_chinese(title, title_font, text_max_w)
    line_h = int(title_size * 1.18)
    total_title_h = line_h * len(title_lines)

    sub_lines: list[str] = []
    sub_font = None
    sub_line_h = 0
    if subtitle:
        sub_size = _fit_font_size(subtitle, text_max_w, base_size=56, min_size=36)
        sub_font = load_font(sub_size)
        sub_lines = _wrap_chinese(subtitle, sub_font, text_max_w)
        sub_line_h = int(sub_size * 1.3)

    pad_top = 56
    pad_bottom = 56
    gap_between = 36
    block_h = total_title_h + (gap_between + sub_line_h * len(sub_lines) if sub_lines else 0)
    box_h = block_h + pad_top + pad_bottom
    box_y2 = CANVAS_H - 110
    box_y1 = box_y2 - box_h
    box_x1, box_x2 = 60, CANVAS_W - 60

    shadow_off = 14
    draw.rounded_rectangle(
        [(box_x1 + shadow_off, box_y1 + shadow_off), (box_x2 + shadow_off, box_y2 + shadow_off)],
        radius=44,
        fill=(40, 40, 40),
    )
    draw.rounded_rectangle(
        [(box_x1, box_y1), (box_x2, box_y2)],
        radius=44,
        fill=(254, 224, 71),
        outline=(40, 40, 40),
        width=6,
    )

    cur_y = box_y1 + pad_top
    for line in title_lines:
        tw = title_font.getbbox(line)[2] - title_font.getbbox(line)[0]
        draw.text(((CANVAS_W - tw) / 2, cur_y), line, font=title_font, fill=(40, 40, 40))
        cur_y += line_h

    if sub_lines and sub_font:
        cur_y += gap_between - line_h + int(line_h * 0.3)
        underline_y = cur_y - 14
        draw.line(
            [(box_x1 + 70, underline_y), (box_x2 - 70, underline_y)],
            fill=(196, 80, 40),
            width=5,
        )
        for line in sub_lines:
            sw = sub_font.getbbox(line)[2] - sub_font.getbbox(line)[0]
            draw.text(((CANVAS_W - sw) / 2, cur_y), line, font=sub_font, fill=(70, 50, 30))
            cur_y += sub_line_h

    _draw_brand_badge(draw, font_size=54)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


def compose_cover_clip(
    *,
    cover_image: Path,
    duration: float,
    out_path: Path,
) -> Path:
    """把封面图变成 N 秒静音视频；采样率与 TTS 段一致，concat 不掉链子。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(cover_image),
        "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate={TTS_SAMPLE_RATE}",
        "-t", f"{duration:.3f}",
        "-r", "30",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(TTS_SAMPLE_RATE), "-ac", "2",
        "-shortest",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"封面 clip 合成失败:\n{proc.stderr[-1500:]}")
    return out_path


def render_outro_page(out_path: Path) -> Path:
    """品牌尾页：超大品牌名 + 引导关注点赞 + 向下箭头。"""
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), BG_COLOR)
    draw = ImageDraw.Draw(canvas)
    _draw_grid(draw)

    brand_font = load_font(_fit_font_size(BRAND_NAME, CANVAS_W - 200, base_size=200, min_size=120))
    bw = brand_font.getbbox(BRAND_NAME)[2] - brand_font.getbbox(BRAND_NAME)[0]
    bx = (CANVAS_W - bw) // 2
    by = 360
    bh = brand_font.getbbox(BRAND_NAME)[3] - brand_font.getbbox(BRAND_NAME)[1]
    # 黄色高亮
    draw.rectangle(
        [(bx - 24, by + int(bh * 0.55)), (bx + bw + 24, by + bh + 30)],
        fill=(254, 224, 71),
    )
    draw.text((bx, by), BRAND_NAME, font=brand_font, fill=(40, 40, 40))

    if BRAND_TAGLINE:
        tag_size = _fit_font_size(BRAND_TAGLINE, CANVAS_W - 200, base_size=68, min_size=44)
        tag_font = load_font(tag_size)
        tw = tag_font.getbbox(BRAND_TAGLINE)[2] - tag_font.getbbox(BRAND_TAGLINE)[0]
        draw.text(((CANVAS_W - tw) // 2, by + bh + 80), BRAND_TAGLINE, font=tag_font, fill=(70, 50, 30))

    # CTA 黄色卡片
    box_x1, box_x2 = 80, CANVAS_W - 80
    box_y1, box_y2 = 1180, 1680
    shadow = 14
    draw.rounded_rectangle(
        [(box_x1 + shadow, box_y1 + shadow), (box_x2 + shadow, box_y2 + shadow)],
        radius=44, fill=(40, 40, 40),
    )
    draw.rounded_rectangle(
        [(box_x1, box_y1), (box_x2, box_y2)],
        radius=44, fill=(254, 224, 71), outline=(40, 40, 40), width=6,
    )

    head_size = _fit_font_size(OUTRO_HEADLINE, CANVAS_W - 260, base_size=110, min_size=72)
    head_font = load_font(head_size)
    hw = head_font.getbbox(OUTRO_HEADLINE)[2] - head_font.getbbox(OUTRO_HEADLINE)[0]
    draw.text(((CANVAS_W - hw) // 2, box_y1 + 90), OUTRO_HEADLINE, font=head_font, fill=(40, 40, 40))

    if OUTRO_SUBLINE:
        sub_size = _fit_font_size(OUTRO_SUBLINE, CANVAS_W - 260, base_size=60, min_size=40)
        sub_font = load_font(sub_size)
        sw = sub_font.getbbox(OUTRO_SUBLINE)[2] - sub_font.getbbox(OUTRO_SUBLINE)[0]
        underline_y = box_y1 + 280
        draw.line(
            [(box_x1 + 80, underline_y), (box_x2 - 80, underline_y)],
            fill=(196, 80, 40), width=5,
        )
        draw.text(((CANVAS_W - sw) // 2, box_y1 + 310), OUTRO_SUBLINE, font=sub_font, fill=(70, 50, 30))

    # 关注箭头
    cx = CANVAS_W // 2
    ay = 1740
    draw.line([(cx, ay), (cx, ay + 90)], fill=(40, 40, 40), width=10)
    draw.polygon(
        [(cx - 38, ay + 70), (cx + 38, ay + 70), (cx, ay + 135)],
        fill=(40, 40, 40),
    )

    _draw_brand_badge(draw, font_size=54)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


def _compose_audio_image_clip(
    *,
    image_path: Path,
    audio_path: Path,
    out_path: Path,
) -> Path:
    """无字幕版的 image+audio 合成（给尾页用，CTA 已经画在图上了）。"""
    duration = ffprobe_duration(audio_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-af", "pan=stereo|c0=c0|c1=c0",
        "-r", "30",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(TTS_SAMPLE_RATE), "-ac", "2",
        "-shortest",
        "-t", f"{duration:.3f}",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"outro clip 合成失败:\n{proc.stderr[-1500:]}")
    return out_path


def ensure_outro_clip() -> Path:
    """生成一次品牌尾页 mp4，按 (brand+tagline+headline+subline+narration) 哈希缓存。"""
    cache_dir = ROOT / "assets" / "outro"
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(
        json.dumps(
            {
                "brand": BRAND_NAME, "tagline": BRAND_TAGLINE,
                "headline": OUTRO_HEADLINE, "subline": OUTRO_SUBLINE,
                "narration": OUTRO_NARRATION,
            },
            ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    clip_path = cache_dir / f"outro_{key}.mp4"
    if clip_path.is_file() and clip_path.stat().st_size > 10_000:
        return clip_path

    print(f"[outro] 首次生成品牌尾页 → {clip_path}", file=sys.stderr)
    png_path = cache_dir / f"outro_{key}.png"
    audio_path = cache_dir / f"outro_{key}.mp3"
    render_outro_page(png_path)
    if not audio_path.is_file() or audio_path.stat().st_size < 1000:
        tts_synthesize(OUTRO_NARRATION, out_path=audio_path)
    _compose_audio_image_clip(image_path=png_path, audio_path=audio_path, out_path=clip_path)
    return clip_path


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
                y=SUBTITLE_Y,
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
        "-af", "pan=stereo|c0=c0|c1=c0",
        "-r", "30",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(TTS_SAMPLE_RATE), "-ac", "2",
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
    # 直接 reencode，避免不同片段编码参数差异导致的播放卡顿/音频乱码
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(TTS_SAMPLE_RATE), "-ac", "2",
        "-movflags", "+faststart",
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

    title_text = str(script.get("title") or "").strip()
    cover_slide = slides[0] if slides else {}
    subtitle_text = str(cover_slide.get("subtitle") or "").strip()
    if title_text:
        print(f"[cover] 准备封面：{title_text}", file=sys.stderr)
        ai_cover_rel = script.get("cover_image")
        if ai_cover_rel:
            ai_cover_path = Path(ai_cover_rel) if Path(ai_cover_rel).is_absolute() else ROOT / ai_cover_rel
        else:
            ai_cover_path = ROOT / "logs" / "images" / script_file.stem / "cover.png"

        cover_png = work_dir / "cover.png"
        if ai_cover_path.is_file():
            # AI 生成的封面：铺满 1080x1920 画布
            render_full_cover(ai_cover_path, out_path=cover_png)
        else:
            # 兜底：用 slide_01 + PIL 叠色块
            print("  ⚠️  cover.png 缺失，回退到 PIL 拼接封面", file=sys.stderr)
            hero_rel = cover_slide.get("image_path")
            if hero_rel:
                hero_path = Path(hero_rel) if Path(hero_rel).is_absolute() else ROOT / hero_rel
            else:
                hero_path = ROOT / "logs" / "images" / script_file.stem / "slide_01.png"
            render_title_cover(
                title=title_text,
                subtitle=subtitle_text,
                out_path=cover_png,
                hero_image=hero_path if hero_path.is_file() else None,
            )
        cover_mp4 = work_dir / "clip_00_cover.mp4"
        compose_cover_clip(cover_image=cover_png, duration=COVER_DURATION_S, out_path=cover_mp4)
        clips.append(cover_mp4)

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
        render_base_canvas(image_path, out_path=base_png)

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

    try:
        outro_clip = ensure_outro_clip()
        clips.append(outro_clip)
        print(f"[outro] 追加品牌尾页：{outro_clip.name}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[outro] ⚠️ 生成尾页失败，跳过：{exc}", file=sys.stderr)

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
