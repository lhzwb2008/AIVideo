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

import categories
from locale_env import locale_logs_dir, locale_output_dir
from paths import ROOT
from research import load_env
from tts_client import synthesize as tts_synthesize

CANVAS_W = 1080
CANVAS_H = 1920
IMAGE_AREA_H = 1500            # 图片占满顶部 78%
BG_COLOR = (251, 246, 228)     # 暖米色，匹配方格纸
TEXT_COLOR = (40, 40, 40)
SUBTITLE_Y = int(os.environ.get("AIVIDEO_SUBTITLE_Y", "1480"))  # 紧贴抖音底部置顶评论/简介浮层上方
SUBTITLE_FONT_SIZE = int(os.environ.get("AIVIDEO_SUBTITLE_FONT_SIZE", "54"))
SUBTITLE_MAX_WIDTH = int(os.environ.get("AIVIDEO_SUBTITLE_MAX_WIDTH", "920"))  # drawtext 可用文本宽度（1080 - 边距 - boxborderw）
IMAGE_TOP_SAFE_Y = int(os.environ.get("AIVIDEO_IMAGE_TOP_SAFE_Y", "150"))

TTS_SAMPLE_RATE = 24000        # 与 DASHSCOPE_TTS_SAMPLE_RATE 保持一致


def _locale_en() -> bool:
    return os.environ.get("AIVIDEO_LOCALE", "zh").strip().lower() in ("en", "english")


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


def max_video_duration_s() -> float:
    """成片最长时长（秒）；英文频道默认 3 分钟。"""
    default = 180.0 if _locale_en() else 0.0
    raw = os.environ.get("AIVIDEO_MAX_VIDEO_DURATION_S", str(default) if default else "").strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def cover_duration_s() -> float:
    """旧版静音封面帧时长；有 cold_open 时不再使用（首帧取自冷开场）。"""
    raw = os.environ.get("AIVIDEO_COVER_DURATION_S", "0.8").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 0.8
    return max(0.0, min(value, 3.0))


def cold_open_duration_s(text: str, audio_path: Path | None = None) -> float:
    """冷开场时长：已有 TTS 时按真实音频走，避免裁掉尾字。"""
    min_s = max(1.5, _env_float("AIVIDEO_COLD_OPEN_MIN_S", 2.5))
    max_s = max(min_s, _env_float("AIVIDEO_COLD_OPEN_MAX_S", 4.5))
    if audio_path and audio_path.is_file():
        dur = ffprobe_duration(audio_path)
        audio_max_s = max(max_s, _env_float("AIVIDEO_COLD_OPEN_AUDIO_MAX_S", 8.0))
        return max(min_s, min(audio_max_s, dur + 0.25))
    n = len((text or "").strip())
    return max(min_s, min(max_s, n / 4.5))


_COLD_OPEN_FX_POOL = ("zoom_punch", "news_flash", "impact")


def cold_open_fx_mode() -> str:
    """冷开场动效：off | zoom_punch | news_flash | impact | zoom_punch_fade | kenburns | auto。"""
    raw = os.environ.get("AIVIDEO_COLD_OPEN_FX", "impact").strip().lower()
    if raw in ("0", "off", "none", "false", "no"):
        return "off"
    if raw in ("1", "on", "yes", "true", "default"):
        return "auto"
    if raw in ("auto", "random"):
        return "auto"
    if raw in _COLD_OPEN_FX_POOL or raw == "zoom_punch_fade":
        return raw
    if raw == "kenburns":
        return "kenburns"
    return "auto"


def pick_cold_open_fx_mode(script_stem: str = "") -> str:
    mode = cold_open_fx_mode()
    if mode == "off":
        return "off"
    if mode != "auto":
        return mode
    seed = hashlib.sha1(f"cold_open_fx|{script_stem}".encode("utf-8")).digest()
    return _COLD_OPEN_FX_POOL[seed[0] % len(_COLD_OPEN_FX_POOL)]


# ============================================================
# 栏目品牌 / 尾页（zh 默认；locale=en 时用英文池）
# ============================================================
_ZH_OUTRO_NARRATION = (
    "我是AI财知道，每天用大白话讲清一个AI和股市热点，A股美股港股都聊。"
    "觉得有用就收藏下来对照看盘用，也欢迎点个关注，下条更新别错过！"
)
_ZH_OUTRO_VARIANTS = [
    _ZH_OUTRO_NARRATION,
    "我是AI财知道，每天用大白话讲一个AI和股市热点。记得收藏对照看盘用，也点个关注，明天同一时间见！",
    "今天的AI和股市为什么就讲到这。觉得有用就收藏下来，对照看盘用，也欢迎关注我别错过下一条。",
    "AI财知道陪你看懂AI和钱的事，A股美股港股都聊。收藏好这条，点关注每天一条不掉队。",
    "就到这。如果这条让你多懂一点，收藏下来有空再看，也欢迎关注我们继续每天更新。",
    "我是AI财知道，专挑值得解释的AI和股市热点。收藏加关注，明天继续陪你看世界。",
]
_EN_OUTRO_VARIANTS = [
    "That's the sketch for today. Save it if useful — and tell me what you'd watch next.",
    "Quick market sketch. Save this for later and follow for the next one.",
    "That's today's sketch. Bookmark it if it helped — follow for daily updates.",
    "Market Sketch signing off. Save this episode and follow for the next move.",
    "That's the wrap. If this clarified anything, save it — tell us what to cover next.",
    "Plain-English markets, one sketch at a time. Save, follow, see you tomorrow.",
]

BRAND_NAME = "AI财知道"
BRAND_TAGLINE = "每天一个 AI 和股市的为什么"
OUTRO_NARRATION = _ZH_OUTRO_NARRATION
OUTRO_HEADLINE = "点赞 · 收藏 · 关注"
OUTRO_SUBLINE = "看懂 AI 和股市的事"
OUTRO_NARRATION_VARIANTS: list[str] = list(_ZH_OUTRO_VARIANTS)


def _load_brand_outro_config() -> None:
    """按 locale / 环境变量刷新品牌与尾页文案（main 入口在 load_env 后再调一次）。"""
    global BRAND_NAME, BRAND_TAGLINE, OUTRO_NARRATION, OUTRO_HEADLINE, OUTRO_SUBLINE
    global OUTRO_NARRATION_VARIANTS
    en = _locale_en()
    if en:
        brand_default = "Market Sketch"
        tagline_default = "US markets in plain English"
        outro_default = _EN_OUTRO_VARIANTS[0]
        headline_default = "Like · Save · Follow"
        subline_default = "US markets in plain English"
        pool_default = _EN_OUTRO_VARIANTS
    else:
        brand_default = "AI财知道"
        tagline_default = "每天一个 AI 和股市的为什么"
        outro_default = _ZH_OUTRO_NARRATION
        headline_default = "点赞 · 收藏 · 关注"
        subline_default = "看懂 AI 和股市的事"
        pool_default = _ZH_OUTRO_VARIANTS

    BRAND_NAME = os.environ.get("AIVIDEO_BRAND_NAME", brand_default).strip()
    BRAND_TAGLINE = os.environ.get("AIVIDEO_BRAND_TAGLINE", tagline_default).strip()
    OUTRO_NARRATION = os.environ.get("AIVIDEO_OUTRO_NARRATION", outro_default).strip()
    OUTRO_HEADLINE = os.environ.get("AIVIDEO_OUTRO_HEADLINE", headline_default).strip()
    OUTRO_SUBLINE = os.environ.get("AIVIDEO_OUTRO_SUBLINE", subline_default).strip()
    raw = os.environ.get("AIVIDEO_OUTRO_NARRATION_VARIANTS", "").strip()
    if raw:
        OUTRO_NARRATION_VARIANTS = [s.strip() for s in raw.split("|") if s.strip()]
    else:
        OUTRO_NARRATION_VARIANTS = list(pool_default)


_load_brand_outro_config()


# ============================================================
# 子栏目主题色：同一主账号下 A股 / 港美股 / AI资讯 / 量化 用不同高亮色 + 角标后缀
# ============================================================
_THEME_ACCENT: tuple[int, int, int] = categories.DEFAULT_ACCENT
_THEME_LABEL: str = ""


def set_theme(category: str | None) -> None:
    """根据子栏目设置当前合成用的高亮色与角标后缀。"""
    global _THEME_ACCENT, _THEME_LABEL
    _THEME_ACCENT = categories.accent_color(category)
    _THEME_LABEL = categories.label_of(category)
    if category:
        print(f"[theme] 子栏目主题：{_THEME_LABEL or category} accent={_THEME_ACCENT}", file=sys.stderr)


def _accent() -> tuple[int, int, int]:
    return _THEME_ACCENT


def _brand_badge_text() -> str:
    return f"{BRAND_NAME} · {_THEME_LABEL}" if (BRAND_NAME and _THEME_LABEL) else BRAND_NAME


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
    """左上角小徽标：栏目主题色 highlight + 黑字品牌名（含子栏目后缀）。"""
    brand_text = _brand_badge_text()
    if not brand_text:
        return
    x = _env_int("AIVIDEO_BRAND_BADGE_X", 86) if x is None else x
    y = _env_int("AIVIDEO_BRAND_BADGE_Y", 150) if y is None else y
    font = load_font(font_size)
    bbox = font.getbbox(brand_text)
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
        radius=16, fill=_accent(), outline=(40, 40, 40), width=3,
    )
    draw.text((box_x1 + pad_x, box_y1 + pad_y - bbox[1]), brand_text, font=font, fill=(40, 40, 40))


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


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """按像素宽度折行（中英文通用）。"""
    return _wrap_chinese(text, font, max_w)


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


def _wrap_english_words(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """英文按词折行，避免在单词中间断开。"""
    words = (text or "").split()
    if not words:
        return []
    lines: list[str] = []
    cur = words[0]
    for word in words[1:]:
        candidate = f"{cur} {word}"
        if font.getbbox(candidate)[2] <= max_w:
            cur = candidate
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines


def _wrap_subtitle_lines(text: str, *, fontsize: int | None = None) -> list[str]:
    """底部分句字幕折行：中文逐字、英文按词，宽度不超过 SUBTITLE_MAX_WIDTH。"""
    phrase = (text or "").strip()
    if not phrase:
        return []
    size = fontsize or SUBTITLE_FONT_SIZE
    font = load_font(size)
    max_w = SUBTITLE_MAX_WIDTH
    if _locale_en():
        return _wrap_english_words(phrase, font, max_w)
    return _wrap_chinese(phrase, font, max_w)


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
    title_lines = _wrap_text(title, title_font, text_max_w)
    line_h = int(title_size * 1.18)
    total_title_h = line_h * len(title_lines)

    sub_lines: list[str] = []
    sub_font = None
    sub_line_h = 0
    if subtitle:
        sub_size = _fit_font_size(subtitle, text_max_w, base_size=56, min_size=36)
        sub_font = load_font(sub_size)
        sub_lines = _wrap_text(subtitle, sub_font, text_max_w)
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
        fill=_accent(),
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


def render_cold_open_frame(
    *,
    cold_open: str,
    out_path: Path,
    hero_image: Path | None = None,
    title: str = "",
) -> Path:
    """冷开场画面：示意图 + 底部大字钩子（只显示 cold_open）。"""
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

    hook = (cold_open or "").strip()
    text_max_w = CANVAS_W - 180
    hook_size = _fit_font_size(hook, text_max_w, base_size=96, min_size=56)
    hook_font = load_font(hook_size)
    hook_lines = _wrap_text(hook, hook_font, text_max_w)
    line_h = int(hook_size * 1.2)
    total_h = line_h * len(hook_lines)

    box_x1, box_x2 = 50, CANVAS_W - 50
    pad_top, pad_bottom = 48, 48
    box_h = total_h + pad_top + pad_bottom
    box_y2 = CANVAS_H - 100
    box_y1 = box_y2 - box_h
    shadow_off = 14
    draw.rounded_rectangle(
        [(box_x1 + shadow_off, box_y1 + shadow_off), (box_x2 + shadow_off, box_y2 + shadow_off)],
        radius=40,
        fill=(40, 40, 40),
    )
    draw.rounded_rectangle(
        [(box_x1, box_y1), (box_x2, box_y2)],
        radius=40,
        fill=_accent(),
        outline=(40, 40, 40),
        width=6,
    )
    cur_y = box_y1 + pad_top
    for line in hook_lines:
        tw = hook_font.getbbox(line)[2] - hook_font.getbbox(line)[0]
        draw.text(((CANVAS_W - tw) / 2, cur_y), line, font=hook_font, fill=(40, 40, 40))
        cur_y += line_h

    if title and title.strip() != hook:
        tag_size = _fit_font_size(title, text_max_w, base_size=40, min_size=28)
        tag_font = load_font(tag_size)
        tw = tag_font.getbbox(title)[2] - tag_font.getbbox(title)[0]
        draw.text(((CANVAS_W - tw) / 2, box_y1 - 56), title, font=tag_font, fill=(70, 50, 30))

    _draw_brand_badge(draw, font_size=50)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


def build_cover_png(
    *,
    out_path: Path,
    title_text: str,
    subtitle_text: str,
    ai_cover_path: Path,
    hero_path: Path,
) -> Path:
    """标准封面图（标题块/AI 全屏），不在画面上叠 cold_open 大字。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if ai_cover_path.is_file():
        render_full_cover(ai_cover_path, out_path=out_path)
    elif title_text:
        if not hero_path.is_file():
            print("  ⚠️  cover.png 缺失，回退到 PIL 拼接封面", file=sys.stderr)
        render_title_cover(
            title=title_text,
            subtitle=subtitle_text,
            out_path=out_path,
            hero_image=hero_path if hero_path.is_file() else None,
        )
    else:
        Image.new("RGB", (CANVAS_W, CANVAS_H), BG_COLOR).save(out_path)
    return out_path


def compose_cold_open_clip(
    *,
    image_path: Path,
    audio_path: Path,
    out_path: Path,
    cold_open_text: str = "",
    work_dir: Path,
    subtitle_delay_s: float = 0.0,
    script_stem: str = "",
) -> Path:
    """冷开场：封面动效 + 底部分句字幕跟读。

    subtitle_delay_s：片头若干秒只播口播、不叠字幕（默认跟 AIVIDEO_COVER_DURATION_S，
    供抖音等平台从前 0.8s 取干净封面帧）。
    """
    duration = cold_open_duration_s(cold_open_text, audio_path)
    text = (cold_open_text or "").strip()
    fallback_len = _subtitle_phrase_max_chars()
    phrases = split_narration(text) or [text[:fallback_len] if text else "…"]
    spans = allocate_phrase_times(phrases, duration)
    delay = max(0.0, subtitle_delay_s)
    fx_mode = pick_cold_open_fx_mode(script_stem)

    work_dir.mkdir(parents=True, exist_ok=True)
    font = font_path()

    if fx_mode == "off":
        filters: list[str] = [_kenburns_filter(0, duration)]
    else:
        filters = [_cold_open_video_filter(duration, fx_mode)]

    for idx, (phrase, (start, end)) in enumerate(zip(phrases, spans)):
        sub_start = max(start, delay)
        if sub_start >= end - 0.02:
            continue
        tf = _make_phrase_textfile(phrase, work_dir / f"cold_phrase_{idx:02d}.txt", fontsize=SUBTITLE_FONT_SIZE)
        filters.append(
            _drawtext_filter(
                textfile=tf,
                font=font,
                fontsize=SUBTITLE_FONT_SIZE,
                y=SUBTITLE_Y,
                start=sub_start,
                end=end,
            )
        )
    filter_chain = ",".join(filters)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path),
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
        raise RuntimeError(f"冷开场 clip 合成失败:\n{proc.stderr[-1500:]}")
    return out_path


def compose_cover_clip(
    *,
    cover_image: Path,
    duration: float,
    out_path: Path,
    audio_path: Path | None = None,
    audio_start_s: float = 0.0,
) -> Path:
    """封面图视频：默认 0.8s 供抖音自动取封面；可叠加第 1 页口播前段，让人声第一时间出现。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(cover_image),
    ]
    if audio_path and audio_path.is_file():
        if audio_start_s > 0:
            cmd += ["-ss", f"{audio_start_s:.3f}"]
        cmd += ["-i", str(audio_path)]
    else:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate={TTS_SAMPLE_RATE}"]
    cmd += [
        "-t", f"{duration:.3f}",
        "-vf", _kenburns_filter(0, duration),
        "-af", "pan=stereo|c0=c0|c1=c0",
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
    # 主题色高亮
    draw.rectangle(
        [(bx - 24, by + int(bh * 0.55)), (bx + bw + 24, by + bh + 30)],
        fill=_accent(),
    )
    draw.text((bx, by), BRAND_NAME, font=brand_font, fill=(40, 40, 40))

    if BRAND_TAGLINE:
        tag_size = _fit_font_size(BRAND_TAGLINE, CANVAS_W - 200, base_size=68, min_size=44)
        tag_font = load_font(tag_size)
        tw = tag_font.getbbox(BRAND_TAGLINE)[2] - tag_font.getbbox(BRAND_TAGLINE)[0]
        draw.text(((CANVAS_W - tw) // 2, by + bh + 80), BRAND_TAGLINE, font=tag_font, fill=(70, 50, 30))

    # CTA 主题色卡片
    box_x1, box_x2 = 80, CANVAS_W - 80
    box_y1, box_y2 = 1180, 1680
    shadow = 14
    draw.rounded_rectangle(
        [(box_x1 + shadow, box_y1 + shadow), (box_x2 + shadow, box_y2 + shadow)],
        radius=44, fill=(40, 40, 40),
    )
    draw.rounded_rectangle(
        [(box_x1, box_y1), (box_x2, box_y2)],
        radius=44, fill=_accent(), outline=(40, 40, 40), width=6,
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
                "theme": _THEME_LABEL, "accent": list(_THEME_ACCENT),
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


def _subtitle_phrase_max_chars() -> int:
    """口播分句上限：中文按字数；英文按字幕单行像素宽度估算（避免 ffmpeg drawtext 溢出）。"""
    if not _locale_en():
        return 18
    font = load_font(SUBTITLE_FONT_SIZE)
    probe = "The quick brown fox jumps over the lazy dog and "
    lo, hi = 16, len(probe)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.getbbox(probe[:mid])[2] <= SUBTITLE_MAX_WIDTH:
            lo = mid
        else:
            hi = mid - 1
    return max(24, lo)


def split_narration(text: str, max_chars: int | None = None) -> list[str]:
    """按标点切句；过长再按字数切。"""
    if max_chars is None:
        max_chars = _subtitle_phrase_max_chars()
    text = (text or "").strip()
    if not text:
        return []
    raw = [p.strip() for p in _PHRASE_SPLIT.split(text) if p.strip()]
    out: list[str] = []
    break_chars = " ,-" if _locale_en() else " 、的了"
    for phrase in raw:
        while len(phrase) > max_chars:
            cut = max_chars
            for i in range(max_chars - 6, max_chars):
                if i < len(phrase) and phrase[i] in break_chars:
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


def _cold_open_zoompan_filter(duration: float, fps: int = 30) -> str:
    """冷开场快推镜：前 ~0.45s 快速放大，后段缓慢漂移。"""
    n = max(2, int(round(duration * fps)))
    punch_frames = min(20, max(10, int(0.45 * fps)))
    zp = (
        f"z='if(lt(on,{punch_frames}),1+on*0.0065,min(1.14,pzoom+0.00022))'"
        ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    )
    return (
        f"scale=2160:3840:flags=lanczos,"
        f"zoompan={zp}:d={n}:s={CANVAS_W}x{CANVAS_H}:fps={fps}"
    )


def _cold_open_news_flash_filter(duration: float, fps: int = 30) -> str:
    """新闻快报感：暗场闪入 + 快速清晰 + 稳定推进。"""
    n = max(2, int(round(duration * fps)))
    settle_frames = min(24, max(12, int(0.55 * fps)))
    zp = (
        f"z='if(lt(on,{settle_frames}),1.10-on*0.0025,min(1.06,pzoom+0.00012))'"
        ":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    )
    return (
        f"scale=2160:3840:flags=lanczos,"
        f"zoompan={zp}:d={n}:s={CANVAS_W}x{CANVAS_H}:fps={fps},"
        "eq=brightness='if(lt(t,0.16),-0.32+t*2.0,0)'"
        ":contrast='if(lt(t,0.45),1.22-t*0.25,1.10)'"
        ":saturation=1.08,"
        "unsharp=5:5:0.8:3:3:0.2,"
        "fade=t=in:st=0:d=0.120"
    )


def _cold_open_impact_filter(duration: float, fps: int = 30) -> str:
    """强冲击版：原色调快推回弹 + 短促抖动，仍不叠额外文字。"""
    n = max(2, int(round(duration * fps)))
    punch_frames = min(12, max(6, int(0.26 * fps)))
    rebound_frames = min(28, max(punch_frames + 6, int(0.70 * fps)))
    zp = (
        f"z='if(lt(on,{punch_frames}),1+on*0.020,"
        f"if(lt(on,{rebound_frames}),1.22-(on-{punch_frames})*0.006,min(1.10,pzoom+0.00010)))'"
        ":x='iw/2-(iw/zoom/2)+if(lt(on,10),sin(on*2.7)*10,0)'"
        ":y='ih/2-(ih/zoom/2)+if(lt(on,10),cos(on*2.4)*8,0)'"
    )
    return (
        f"scale=2160:3840:flags=lanczos,"
        f"zoompan={zp}:d={n}:s={CANVAS_W}x{CANVAS_H}:fps={fps},"
        "unsharp=5:5:0.55:3:3:0.15"
    )


def _cold_open_video_filter(duration: float, mode: str, fps: int = 30) -> str:
    if mode == "kenburns":
        base = _kenburns_filter(0, duration, fps=fps)
    elif mode == "news_flash":
        base = _cold_open_news_flash_filter(duration, fps=fps)
    elif mode == "impact":
        base = _cold_open_impact_filter(duration, fps=fps)
    else:
        base = _cold_open_zoompan_filter(duration, fps=fps)
    if mode == "zoom_punch_fade":
        fade_d = min(0.35, max(0.12, duration * 0.12))
        base = f"{base},fade=t=in:st=0:d={fade_d:.3f}"
    return base


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


def _make_phrase_textfile(phrase: str, out: Path, *, fontsize: int | None = None) -> Path:
    lines = _wrap_subtitle_lines(phrase, fontsize=fontsize)
    out.write_text("\n".join(lines) if lines else phrase, encoding="utf-8")
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
    audio_start_s: float = 0.0,
) -> Path:
    if audio_start_s > 0:
        duration = max(0.05, ffprobe_duration(audio_path) - audio_start_s)
    else:
        duration = ffprobe_duration(audio_path)
    fallback_len = _subtitle_phrase_max_chars()
    phrases = split_narration(narration) or [narration[:fallback_len]]
    spans = allocate_phrase_times(phrases, duration)

    work_dir.mkdir(parents=True, exist_ok=True)
    font = font_path()

    filters: list[str] = [_kenburns_filter(kenburns_direction, duration)]
    for idx, (phrase, (start, end)) in enumerate(zip(phrases, spans)):
        tf = _make_phrase_textfile(phrase, work_dir / f"phrase_{idx:02d}.txt", fontsize=SUBTITLE_FONT_SIZE)
        filters.append(
            _drawtext_filter(
                textfile=tf,
                font=font,
                fontsize=SUBTITLE_FONT_SIZE,
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
    ]
    if audio_start_s > 0:
        cmd += ["-ss", f"{audio_start_s:.3f}", "-i", str(audio_path)]
    else:
        cmd += ["-i", str(audio_path)]
    cmd += [
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

    load_env()
    _load_brand_outro_config()
    if _locale_en():
        try:
            from us_voice import apply_voice_env, resolve_voice

            vid = apply_voice_env()
            _, vcfg = resolve_voice(vid)
            print(
                f"[compose] TTS: doubao {os.environ.get('VOLCENGINE_TTS_RESOURCE_ID', '')} "
                f"音色: {os.environ.get('VOLCENGINE_TTS_SPEAKER', '')} ({vcfg.get('name', vid)})",
                file=sys.stderr,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[compose] US 音色配置失败: {exc}", file=sys.stderr)
    set_theme(categories.resolve_category(script, os.environ.get("AIVIDEO_CATEGORY")))

    if not _locale_en():
        try:
            from research import print_douyin_pre_publish_scan

            print_douyin_pre_publish_scan(script)
        except Exception as exc:  # noqa: BLE001
            print(f"[douyin预审] 跳过：{exc}", file=sys.stderr)

    work_dir = (work_dir or locale_logs_dir() / "compose" / script_file.stem)
    work_dir.mkdir(parents=True, exist_ok=True)

    total = len(slides)
    clips: list[Path] = []

    title_text = str(script.get("title") or "").strip()
    cover_slide = slides[0] if slides else {}
    subtitle_text = str(cover_slide.get("subtitle") or "").strip()
    cold_open_text = str(script.get("cold_open") or "").strip()

    ai_cover_rel = script.get("cover_image")
    if ai_cover_rel:
        ai_cover_path = Path(ai_cover_rel) if Path(ai_cover_rel).is_absolute() else ROOT / ai_cover_rel
    else:
        ai_cover_path = locale_logs_dir() / "images" / script_file.stem / "cover.png"
    hero_rel = cover_slide.get("image_path")
    if hero_rel:
        hero_path = Path(hero_rel) if Path(hero_rel).is_absolute() else ROOT / hero_rel
    else:
        hero_path = locale_logs_dir() / "images" / script_file.stem / "slide_01.png"
    cover_png = work_dir / "cover.png"
    if cold_open_text:
        print(f"[cold_open] 口播：{cold_open_text}", file=sys.stderr)
        build_cover_png(
            out_path=cover_png,
            title_text=title_text,
            subtitle_text=subtitle_text,
            ai_cover_path=ai_cover_path,
            hero_path=hero_path,
        )
        subtitle_delay_s = cover_duration_s()
        fx_mode = pick_cold_open_fx_mode(script_file.stem)
        if fx_mode == "off":
            fx_note = "动效关"
        else:
            fx_note = f"动效 {fx_mode}"
        if subtitle_delay_s > 0:
            print(
                f"  封面保持原标题样式；前 {subtitle_delay_s:.2f}s 画面动效无字幕，"
                f"之后底部分句字幕跟读（{fx_note}）",
                file=sys.stderr,
            )
        else:
            print(f"  封面保持原标题样式，底部分句字幕跟读（{fx_note}）", file=sys.stderr)
        audio_cold = work_dir / "audio_cold_open.mp3"
        cold_phrase_dir = work_dir / "cold_phrases"
        if not skip_tts or not audio_cold.is_file():
            print("   冷开场 TTS …", file=sys.stderr)
            tts_synthesize(cold_open_text, out_path=audio_cold)
        cold_mp4 = work_dir / "clip_00_cold_open.mp4"
        compose_cold_open_clip(
            image_path=cover_png,
            audio_path=audio_cold,
            out_path=cold_mp4,
            cold_open_text=cold_open_text,
            work_dir=cold_phrase_dir,
            subtitle_delay_s=subtitle_delay_s,
            script_stem=script_file.stem,
        )
        clips.append(cold_mp4)
        dur = cold_open_duration_s(cold_open_text, audio_cold)
        print(f"  冷开场 {dur:.2f}s", file=sys.stderr)
    elif title_text:
        print(f"[cover] 准备封面：{title_text}", file=sys.stderr)
        build_cover_png(
            out_path=cover_png,
            title_text=title_text,
            subtitle_text=subtitle_text,
            ai_cover_path=ai_cover_path,
            hero_path=hero_path,
        )
        cover_dur = cover_duration_s()
        if cover_dur > 0:
            cover_mp4 = work_dir / "clip_00_cover.mp4"
            compose_cover_clip(cover_image=cover_png, duration=cover_dur, out_path=cover_mp4)
            clips.append(cover_mp4)
            print(f"  静音封面 {cover_dur:.2f}s（建议脚本补 cold_open）", file=sys.stderr)

    for i, slide in enumerate(slides, start=1):
        print(f"[{i}/{total}] 合成单段：{slide.get('chapter_title') or slide.get('headline') or ''}", file=sys.stderr)

        image_rel = slide.get("image_path")
        if image_rel:
            image_path = Path(image_rel) if Path(image_rel).is_absolute() else ROOT / image_rel
        else:
            image_path = locale_logs_dir() / "images" / script_file.stem / f"slide_{i:02d}.png"
        if not image_path.is_file():
            print(f"  ⚠️  缺图: {image_path}", file=sys.stderr)
            image_path = work_dir / f"missing_{i}.png"
            Image.new("RGB", (CANVAS_W, IMAGE_AREA_H), BG_COLOR).save(image_path)

        base_png = work_dir / f"base_{i:02d}.png"
        render_base_canvas(image_path, out_path=base_png)

        narration = str(slide.get("narration") or "")
        audio_path = work_dir / f"audio_{i:02d}.mp3"
        if not audio_path.is_file():
            print(f"   TTS …", file=sys.stderr)
            tts_synthesize(narration, out_path=audio_path)
        elif not skip_tts:
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

    output = output or (locale_output_dir() / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4")
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
    dur_s = ffprobe_duration(output)
    max_dur = max_video_duration_s()
    if max_dur > 0 and dur_s > max_dur + 0.5:
        print(
            f"⚠️  成片 {dur_s:.1f}s 超过上限 {max_dur:.0f}s（AIVIDEO_MAX_VIDEO_DURATION_S）；"
            "建议缩短脚本口播或减页数",
            file=sys.stderr,
        )
    elif max_dur > 0:
        print(f"[duration] {dur_s:.1f}s / {max_dur:.0f}s", file=sys.stderr)
    print(f"完成：{output} ({output.stat().st_size//1024} KB)", file=sys.stderr)

    last_video = locale_logs_dir() / "last_video.txt"
    last_video.write_text(str(output) + "\n", encoding="utf-8")
    manifest = locale_logs_dir() / "video_manifest.jsonl"
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
    _load_brand_outro_config()
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
