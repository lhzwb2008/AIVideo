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
import random
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
SUBTITLE_Y = int(os.environ.get("AIVIDEO_SUBTITLE_Y", "1480"))  # 紧贴抖音底部置顶评论/简介浮层上方
IMAGE_TOP_SAFE_Y = int(os.environ.get("AIVIDEO_IMAGE_TOP_SAFE_Y", "150"))

TTS_SAMPLE_RATE = 24000        # 与 DASHSCOPE_TTS_SAMPLE_RATE 保持一致


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def cover_duration_s() -> float:
    """开场静音封面时长（秒）。过长易被当成卡住；0 表示跳过独立封面段。"""
    raw = os.environ.get("AIVIDEO_COVER_DURATION_S", "0.8").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 0.8
    return max(0.0, min(value, 3.0))

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

# 尾页旁白每条视频随机选一条，避免每天产出末尾 mp4 字节完全相同被抖音判重复。
# 用 "|" 分隔自定义变体，否则使用下面的默认池。
_OUTRO_VARIANTS_RAW = os.environ.get("AIVIDEO_OUTRO_NARRATION_VARIANTS", "").strip()
if _OUTRO_VARIANTS_RAW:
    OUTRO_NARRATION_VARIANTS = [s.strip() for s in _OUTRO_VARIANTS_RAW.split("|") if s.strip()]
else:
    OUTRO_NARRATION_VARIANTS = [
        OUTRO_NARRATION,
        "我是AI财知道，每天用大白话讲一个AI财经热点。点个关注，明天同一时间见！",
        "今天的AI财经为什么就讲到这。觉得有用就点赞收藏，关注我别错过下一条。",
        "AI财知道陪你看懂AI和钱的事。点关注，每天一条，新鲜的认知不掉队。",
        "就到这。如果这条让你多懂一点，麻烦点个赞，关注我们继续每天更新。",
        "我是AI财知道，专挑值得解释的AI财经热点。点赞关注，明天继续陪你看世界。",
    ]


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
    x: int | None = None,
    y: int | None = None,
    font_size: int = 46,
) -> None:
    """左上角小徽标：黄色 highlight + 黑字品牌名。栏目品牌透出。"""
    if not BRAND_NAME:
        return
    x = _env_int("AIVIDEO_BRAND_BADGE_X", 86) if x is None else x
    y = _env_int("AIVIDEO_BRAND_BADGE_Y", 150) if y is None else y
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
        top_safe = max(0, min(IMAGE_TOP_SAFE_Y, IMAGE_AREA_H - 400))
        available_h = IMAGE_AREA_H - top_safe
        ratio = min(CANVAS_W / img.width, available_h / img.height)
        new_w = int(img.width * ratio)
        new_h = int(img.height * ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        x = (CANVAS_W - new_w) // 2
        y = top_safe + (available_h - new_h) // 2
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
        "-vf", _kenburns_filter(0, duration),
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
        "-vf", _kenburns_filter(0, duration),
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


def ensure_outro_clip(*, script_stem: str = "") -> Path:
    """生成品牌尾页 mp4。按 (日期 + 脚本) 选 narration 变体，并把它进 hash key，
    确保每天/每条视频的尾页 mp4 字节都不同，避免抖音对相同尾页打"重复内容"。
    同一脚本同一天仍然命中缓存（重跑 compose 不浪费 TTS）。
    """
    cache_dir = ROOT / "assets" / "outro"
    cache_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y%m%d")
    seed = hashlib.sha1(f"{today}|{script_stem}".encode("utf-8")).digest()
    variants = OUTRO_NARRATION_VARIANTS or [OUTRO_NARRATION]
    narration = variants[seed[0] % len(variants)]

    key = hashlib.sha1(
        json.dumps(
            {
                "brand": BRAND_NAME, "tagline": BRAND_TAGLINE,
                "headline": OUTRO_HEADLINE, "subline": OUTRO_SUBLINE,
                "narration": narration, "date": today, "stem": script_stem,
            },
            ensure_ascii=False, sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:12]
    clip_path = cache_dir / f"outro_{key}.mp4"
    if clip_path.is_file() and clip_path.stat().st_size > 10_000:
        return clip_path

    print(f"[outro] 生成尾页变体 → {clip_path.name}（{narration[:18]}…）", file=sys.stderr)
    png_path = cache_dir / f"outro_{key}.png"
    audio_path = cache_dir / f"outro_{key}.mp3"
    render_outro_page(png_path)
    if not audio_path.is_file() or audio_path.stat().st_size < 1000:
        tts_synthesize(narration, out_path=audio_path)
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


def _kenburns_filter(direction: int, duration: float, fps: int = 30) -> str:
    """生成 Ken Burns 缓推/缓拉/平移滤镜，避免画面纯静态被抖音判 PPT 低质。

    direction: 0=推近, 1=拉远, 2=右平移, 3=下平移；按 slide index 轮换。
    """
    n = max(2, int(round(duration * fps)))
    if direction % 4 == 0:
        zp = (
            "z='min(pzoom+0.0008,1.08)'"
            ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        )
    elif direction % 4 == 1:
        zp = (
            "z='if(eq(on,0),1.08,max(1.0,pzoom-0.0008))'"
            ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        )
    elif direction % 4 == 2:
        zp = (
            "z=1.06"
            f":x='iw/2-(iw/zoom/2)+(on/{n})*80-40'"
            ":y='ih/2-(ih/zoom/2)'"
        )
    else:
        zp = (
            "z=1.06"
            ":x='iw/2-(iw/zoom/2)'"
            f":y='ih/2-(ih/zoom/2)+(on/{n})*80-40'"
        )
    return (
        f"scale=2160:3840:flags=lanczos,"
        f"zoompan={zp}:d={n}:s={CANVAS_W}x{CANVAS_H}:fps={fps}"
    )


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
        f"y={y}-text_h/2",
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
    kenburns_direction: int = 0,
) -> Path:
    duration = ffprobe_duration(audio_path)
    phrases = split_narration(narration) or [narration[:18]]
    spans = allocate_phrase_times(phrases, duration)

    work_dir.mkdir(parents=True, exist_ok=True)
    font = font_path()

    filters: list[str] = [_kenburns_filter(kenburns_direction, duration)]
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
    filter_chain = ",".join(filters)

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


# 页间转场池：每页轮换不同 xfade 类型，避免单一过渡也成模板特征
_XFADE_TRANSITIONS = [
    "fade", "slideleft", "slideright", "slideup",
    "wiperight", "fade", "circleopen", "smoothleft",
]
XFADE_DURATION = float(os.environ.get("AIVIDEO_XFADE_DURATION", "0.4"))


def concat_clips(clips: list[Path], out_path: Path, work_dir: Path) -> Path:
    """先尝试 xfade + acrossfade 软转场；失败则回退到硬切 concat。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if len(clips) == 1:
        shutil.copy(clips[0], out_path)
        return out_path
    try:
        return _concat_clips_xfade(clips, out_path)
    except Exception as exc:  # noqa: BLE001
        print(f"[concat] xfade 失败，回退硬切：{exc}", file=sys.stderr)
        return _concat_clips_hardcut(clips, out_path, work_dir)


def _output_jitter_args() -> list[str]:
    """B6: 给最终 mp4 引入元数据/编码参数扰动，打破容器与帧序列指纹。"""
    crf = random.randint(19, 22)
    gop = random.choice([60, 75, 90, 120, 150])
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    title_suffix = hashlib.sha1(os.urandom(8)).hexdigest()[:8]
    return [
        "-crf", str(crf),
        "-g", str(gop),
        "-metadata", f"creation_time={created}",
        "-metadata", f"title=aicaizhidao-{title_suffix}",
        "-metadata", f"comment=build-{title_suffix}",
    ]


def _concat_clips_xfade(clips: list[Path], out_path: Path) -> Path:
    durations = [ffprobe_duration(c) for c in clips]
    t = max(0.15, min(XFADE_DURATION, 1.0))
    # 任何片段比转场还短时退回硬切，避免 offset 算错
    if min(durations) <= t + 0.05:
        raise RuntimeError(f"存在过短片段（min={min(durations):.2f}s ≤ {t+0.05:.2f}s）")

    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", str(c)]

    filters: list[str] = []
    prev_v, prev_a = "0:v", "0:a"
    cum = durations[0]
    for i in range(1, len(clips)):
        offset = cum - t
        trans = _XFADE_TRANSITIONS[(i - 1) % len(_XFADE_TRANSITIONS)]
        v_out = f"v{i}"
        a_out = f"a{i}"
        filters.append(
            f"[{prev_v}][{i}:v]xfade=transition={trans}:duration={t}:offset={offset:.3f}[{v_out}]"
        )
        filters.append(
            f"[{prev_a}][{i}:a]acrossfade=d={t}:c1=tri:c2=tri[{a_out}]"
        )
        prev_v, prev_a = v_out, a_out
        cum += durations[i] - t

    filter_complex = ";".join(filters)
    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{prev_v}]", "-map", f"[{prev_a}]",
        "-r", "30",
        "-c:v", "libx264", "-preset", "medium",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(TTS_SAMPLE_RATE), "-ac", "2",
        "-movflags", "+faststart",
        *_output_jitter_args(),
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"xfade concat 失败:\n{proc.stderr[-1800:]}")
    return out_path


def _concat_clips_hardcut(clips: list[Path], out_path: Path, work_dir: Path) -> Path:
    list_file = work_dir / "concat.txt"
    list_file.write_text(
        "\n".join(f"file '{c.resolve()}'" for c in clips) + "\n",
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c:v", "libx264", "-preset", "medium",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(TTS_SAMPLE_RATE), "-ac", "2",
        "-movflags", "+faststart",
        *_output_jitter_args(),
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg concat 失败:\n{proc.stderr[-1500:]}")
    return out_path


_BGM_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}


def _bgm_dir() -> Path:
    raw = os.environ.get("AIVIDEO_BGM_DIR", "assets/bgm").strip()
    return Path(raw) if Path(raw).is_absolute() else ROOT / raw


def _bgm_enabled() -> bool:
    raw = os.environ.get("AIVIDEO_BGM_ENABLED", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def select_bgm(script_stem: str) -> Path | None:
    """按日期和脚本稳定轮换曲库，避免每条都使用同一首。"""
    if not _bgm_enabled():
        return None
    bgm_dir = _bgm_dir()
    if not bgm_dir.is_dir():
        return None
    tracks = sorted(p for p in bgm_dir.iterdir() if p.is_file() and p.suffix.lower() in _BGM_EXTS)
    if not tracks:
        return None
    seed = hashlib.sha1(f"{datetime.now().strftime('%Y%m%d')}|{script_stem}".encode("utf-8")).digest()
    return tracks[int.from_bytes(seed[:4], "big") % len(tracks)]


def mix_bgm(
    *,
    video_path: Path,
    bgm_path: Path,
    out_path: Path,
) -> Path:
    """给最终成片铺一层可听但不抢人声的低音量 BGM。"""
    duration = ffprobe_duration(video_path)
    volume = max(0.0, min(_env_float("AIVIDEO_BGM_VOLUME", 0.35), 1.0))
    fade = max(0.0, min(_env_float("AIVIDEO_BGM_FADE_S", 1.2), max(0.0, duration / 2)))
    voice_volume = max(0.1, min(_env_float("AIVIDEO_BGM_VOICE_VOLUME", 1.0), 2.0))
    duck_enabled = os.environ.get("AIVIDEO_BGM_DUCKING", "0").strip().lower() not in {"0", "false", "no", "off"}
    duck_threshold = _env_float("AIVIDEO_BGM_DUCK_THRESHOLD", 0.12)
    duck_ratio = max(1.0, _env_float("AIVIDEO_BGM_DUCK_RATIO", 2.5))

    fade_out_start = max(0.0, duration - fade)
    bgm_filter = (
        f"[1:a]aformat=channel_layouts=stereo,volume={volume:.4f},"
        f"afade=t=in:st=0:d={fade:.3f},"
        f"afade=t=out:st={fade_out_start:.3f}:d={fade:.3f}[bgm0]"
    )
    voice_filter = f"[0:a]aformat=channel_layouts=stereo,volume={voice_volume:.4f}[voice]"
    filters = [voice_filter, bgm_filter]
    if duck_enabled:
        filters.append(
            f"[bgm0][voice]sidechaincompress=threshold={duck_threshold:.4f}:"
            f"ratio={duck_ratio:.2f}:attack=80:release=350[bgm]"
        )
    else:
        filters.append("[bgm0]anull[bgm]")
    mix_filter = "[voice][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
    filters.append(mix_filter)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-stream_loop", "-1", "-i", str(bgm_path),
        "-filter_complex", ";".join(filters),
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(TTS_SAMPLE_RATE), "-ac", "2",
        "-shortest",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"BGM 混音失败:\n{proc.stderr[-1800:]}")
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
        cover_dur = cover_duration_s()
        if cover_dur > 0:
            cover_mp4 = work_dir / "clip_00_cover.mp4"
            compose_cover_clip(cover_image=cover_png, duration=cover_dur, out_path=cover_mp4)
            clips.append(cover_mp4)
            print(f"  封面停留 {cover_dur:.2f}s（可用 AIVIDEO_COVER_DURATION_S 调整）", file=sys.stderr)
        else:
            print("  跳过独立封面段（AIVIDEO_COVER_DURATION_S=0）", file=sys.stderr)

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
            kenburns_direction=i - 1,
        )
        clips.append(clip_path)

    try:
        outro_clip = ensure_outro_clip(script_stem=script_file.stem)
        clips.append(outro_clip)
        print(f"[outro] 追加品牌尾页：{outro_clip.name}", file=sys.stderr)
    except Exception as exc:  # noqa: BLE001
        print(f"[outro] ⚠️ 生成尾页失败，跳过：{exc}", file=sys.stderr)

    output = output or (ROOT / "output" / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"[concat] {len(clips)} 段 → {output}", file=sys.stderr)
    bgm = select_bgm(script_file.stem)
    if bgm:
        voice_only = work_dir / f"{output.stem}_voice_only.mp4"
        concat_clips(clips, voice_only, work_dir)
        print(f"[bgm] 混入背景音乐：{bgm.name}", file=sys.stderr)
        mix_bgm(video_path=voice_only, bgm_path=bgm, out_path=output)
    else:
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
            "bgm": str(bgm) if bgm else "",
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
