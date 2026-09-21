"""正文页标注：闪光短箭头圈一下或划一条线。"""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

from PIL import Image, ImageDraw

from llm_vision_client import vision_chat

CANVAS_W = 1080
CANVAS_H = 1920
# 避开左上角标、底部字幕带
_SAFE = (90, 280, 990, 1320)

_CACHE_VER = 2


def enabled() -> bool:
    raw = os.environ.get("AIVIDEO_LECTURE_POINTER", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _vision_enabled() -> bool:
    raw = os.environ.get("AIVIDEO_LECTURE_POINTER_VISION", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _vision_model() -> str:
    return (
        os.environ.get("AIVIDEO_POINTER_VISION_MODEL", "").strip()
        or "qwen-vl-plus"
    )


def _clamp_xy(x: float, y: float) -> tuple[float, float]:
    x1, y1, x2, y2 = _SAFE
    return (min(max(x, x1), x2), min(max(y, y1), y2))


def _fallback_points(n: int) -> list[tuple[float, float]]:
    """视觉定位失败时：在示意图区按阅读顺序落点。"""
    if n <= 0:
        return []
    ys = [340 + i * (1180 / max(1, n - 1)) for i in range(n)] if n > 1 else [720.0]
    out: list[tuple[float, float]] = []
    for i, y in enumerate(ys):
        x = 720.0 if i % 2 == 0 else 390.0
        out.append(_clamp_xy(x, y))
    return out


def _strip_fence(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_items(raw: str) -> list[dict]:
    text = _strip_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            start, end = text.find("["), text.rfind("]")
            if start >= 0 and end > start:
                data = {"items": json.loads(text[start : end + 1])}
            else:
                return []
        else:
            data = json.loads(text[start : end + 1])
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        items = data.get("items") or data.get("points") or []
        return [x for x in items if isinstance(x, dict)]
    return []


def _norm_label(s: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff％%.0-9]+", "", s or "")


def locate_label_points(
    canvas_png: Path,
    labels: list[str],
    *,
    cache_path: Path,
) -> list[tuple[str, float, float, float, float]]:
    """定位各要点，返回 (label, cx, cy, w, h)，坐标相对 1080x1920 底图。"""
    labels = [str(t).strip() for t in labels if str(t).strip()]
    if not labels:
        return []
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("v") == _CACHE_VER and cached.get("labels") == labels:
                pts = cached.get("points") or []
                if len(pts) == len(labels) and all("w" in p and "h" in p for p in pts):
                    return [
                        (
                            p["text"],
                            float(p["x"]),
                            float(p["y"]),
                            float(p["w"]),
                            float(p["h"]),
                        )
                        for p in pts
                    ]
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass

    points = _fallback_points(len(labels))
    paired = [
        (lab, x, y, max(48.0, len(_norm_label(lab)) * 22.0), 36.0)
        for lab, (x, y) in zip(labels, points)
    ]
    if _vision_enabled() and canvas_png.is_file():
        try:
            paired = _locate_with_vision(canvas_png, labels) or paired
        except Exception as exc:  # noqa: BLE001
            print(f"  [pointer] 视觉定位失败，用折线走位：{exc}", flush=True)
    if canvas_png.is_file():
        try:
            paired = _snap_to_color_blobs(canvas_png, paired)
        except Exception as exc:  # noqa: BLE001
            print(f"  [pointer] 色块对齐失败，沿用视觉框：{exc}", flush=True)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "v": _CACHE_VER,
                "labels": labels,
                "points": [
                    {"text": t, "x": x, "y": y, "w": bw, "h": bh}
                    for t, x, y, bw, bh in paired
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return paired


def _locate_with_vision(
    canvas_png: Path, labels: list[str]
) -> list[tuple[str, float, float, float, float]] | None:
    thumb = canvas_png.with_name(canvas_png.stem + "_pointer_thumb.jpg")
    im = Image.open(canvas_png).convert("RGB")
    tw = 720
    scale = tw / im.width
    th = max(1, int(im.height * scale))
    im.resize((tw, th), Image.Resampling.LANCZOS).save(thumb, "JPEG", quality=82)
    joined = "、".join(f"「{t}」" for t in labels)
    user = (
        f"这是一张 {tw}x{th} 的竖屏手绘财经漫画缩略图。"
        f"请给出这些手写要点的紧致包围盒（相对这张 {tw}x{th} 图）：{joined}。"
        "cx,cy 是该手写标签（黄底或紫底色块，或纯手写字）的视觉中心；"
        "w,h 必须是刚好包住这几个字的宽高，不要把箭头、插画、地球、建筑框进去。"
        "忽略左上角品牌徽标、页码、底部空白。"
        '只返回 JSON：{"items":[{"text":"原文字","cx":123,"cy":456,"w":80,"h":32}]}'
    )
    raw = vision_chat(
        system="你在手绘漫画上定位中文手写要点的包围盒。只返回 JSON。",
        user_text=user,
        screenshot=thumb,
        model=_vision_model(),
        max_tokens=500,
    )
    items = _parse_items(raw)
    by_norm: dict[str, dict] = {}
    for it in items:
        key = _norm_label(str(it.get("text") or it.get("label") or ""))
        if key:
            by_norm[key] = it
    fallback = _fallback_points(len(labels))
    out: list[tuple[str, float, float, float, float]] = []
    for i, lab in enumerate(labels):
        it = by_norm.get(_norm_label(lab))
        est_w = max(48.0, len(_norm_label(lab)) * 22.0)
        est_h = 36.0
        if it is None:
            x, y = fallback[i]
            out.append((lab, x, y, est_w, est_h))
            continue
        try:
            cx = float(it.get("cx", it.get("x"))) / scale
            cy = float(it.get("cy", it.get("y"))) / scale
            bw = float(it.get("w") or it.get("width") or 0) / scale
            bh = float(it.get("h") or it.get("height") or 0) / scale
        except (KeyError, TypeError, ValueError):
            x, y = fallback[i]
            out.append((lab, x, y, est_w, est_h))
            continue
        cx, cy = _clamp_xy(cx, cy)
        if bw < 12:
            bw = est_w
        if bh < 10:
            bh = est_h
        bw = min(360.0, max(36.0, bw))
        bh = min(120.0, max(20.0, bh))
        out.append((lab, cx, cy, bw, bh))
    print(f"  [pointer] 视觉定位 {len(out)} 个要点（含包围盒）", flush=True)
    return out


def label_holds(
    labels: list[str],
    phrases: list[str],
    spans: list[tuple[float, float]],
    duration: float,
) -> list[tuple[float, float, int]]:
    """口播期间按顺序把时间均分给各要点。"""
    del phrases, spans
    n = len(labels)
    if n <= 0 or duration <= 0:
        return []
    return _even_holds(n, float(duration))


def _even_holds(n: int, duration: float) -> list[tuple[float, float, int]]:
    out: list[tuple[float, float, int]] = []
    for i in range(n):
        t0 = duration * i / n
        t1 = duration if i == n - 1 else duration * (i + 1) / n
        out.append((t0, t1, i))
    return out


def _mark_kind(label: str) -> str:
    """短词圈住，长句在字下划黄线。"""
    return "circle" if len(_norm_label(label)) <= 5 else "line"


def _color_blobs(im: Image.Image) -> list[tuple[float, float, float, float]]:
    """黄底/紫底手写标签的包围盒，用来把圈和线卡在字上。"""
    scale = 4
    small = im.resize((im.width // scale, im.height // scale), Image.Resampling.NEAREST).convert("RGB")
    sw, sh = small.size
    px = small.load()
    mask = [[False] * sw for _ in range(sh)]
    for y in range(sh):
        for x in range(sw):
            if y * scale < 230 and x * scale < 430:
                continue
            r, g, b = px[x, y]
            yellow = r > 175 and g > 155 and b < 175 and (r + g) > b * 2.2 and (r - b) > 25
            purple = (
                r > 130
                and b > 145
                and g > 100
                and b >= g - 20
                and (b + r) > g * 1.9
                and not (r > 200 and g > 200 and b > 200)
            )
            mask[y][x] = yellow or purple
    seen = [[False] * sw for _ in range(sh)]
    blobs: list[tuple[float, float, float, float]] = []
    for y in range(sh):
        for x in range(sw):
            if not mask[y][x] or seen[y][x]:
                continue
            stack = [(x, y)]
            seen[y][x] = True
            xs: list[int] = []
            ys: list[int] = []
            while stack:
                cx, cy = stack.pop()
                xs.append(cx)
                ys.append(cy)
                for nx, ny in ((cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)):
                    if 0 <= nx < sw and 0 <= ny < sh and mask[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        stack.append((nx, ny))
            minx, maxx = min(xs) * scale, (max(xs) + 1) * scale
            miny, maxy = min(ys) * scale, (max(ys) + 1) * scale
            bw, bh = float(maxx - minx), float(maxy - miny)
            if len(xs) < 35 or bw < 36 or bh < 16 or bw > 520 or bh > 180:
                continue
            blobs.append(((minx + maxx) / 2.0, (miny + maxy) / 2.0, bw, bh))
    return blobs


def _local_ink_box(
    im: Image.Image,
    lab: str,
    cx: float,
    cy: float,
    est_w: float,
) -> tuple[str, float, float, float, float] | None:
    """视觉点落在字附近空白时，用最近的一行黑字把框收紧。"""
    px = im.load()
    w, h = im.size
    x0 = max(40, int(cx - est_w * 0.9))
    x1 = min(w - 40, int(cx + est_w * 0.9))
    y0 = max(260, int(cy - 110))
    y1 = min(1400, int(cy + 40))
    if x1 - x0 < 40 or y1 - y0 < 20:
        return None
    dark_rows: list[tuple[int, int]] = []
    for y in range(y0, y1):
        c = 0
        for x in range(x0, x1):
            r, g, b = px[x, y]
            if 0.299 * r + 0.587 * g + 0.114 * b < 80:
                c += 1
        if c >= 8:
            dark_rows.append((y, c))
    if not dark_rows:
        return None
    runs: list[tuple[int, int]] = []
    rs, re = dark_rows[0][0], dark_rows[0][0]
    prev = dark_rows[0][0]
    for y, _c in dark_rows[1:]:
        if y <= prev + 2:
            re = y
        else:
            runs.append((rs, re))
            rs, re = y, y
        prev = y
    runs.append((rs, re))
    y_a, y_b = min(runs, key=lambda ab: abs((ab[0] + ab[1]) / 2 - cy))
    xs: list[int] = []
    ys: list[int] = []
    for y in range(y_a, y_b + 1):
        for x in range(x0, x1):
            r, g, b = px[x, y]
            if 0.299 * r + 0.587 * g + 0.114 * b < 80:
                xs.append(x)
                ys.append(y)
    if len(xs) < 30:
        return None
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    bw, bh = float(maxx - minx), float(maxy - miny)
    if bw < 36 or bh < 12 or bw > 400 or bh > 80:
        return None
    ncx, ncy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    ncx, ncy = _clamp_xy(ncx, ncy)
    return (lab, ncx, ncy, bw, max(20.0, bh))


def _snap_to_color_blobs(
    canvas_png: Path,
    paired: list[tuple[str, float, float, float, float]],
) -> list[tuple[str, float, float, float, float]]:
    im = Image.open(canvas_png).convert("RGB")
    blobs = _color_blobs(im)
    used: set[int] = set()
    out: list[tuple[str, float, float, float, float]] = []
    snapped = [False] * len(paired)
    max_d2 = 90.0 ** 2
    for li, (lab, cx, cy, w, h) in enumerate(paired):
        est_w = max(48.0, len(_norm_label(lab)) * 22.0)
        best_i: int | None = None
        best_d = max_d2
        for i, (bx, by, bw, bh) in enumerate(blobs):
            if i in used:
                continue
            if bw < est_w * 0.55 or bw > est_w * 3.2:
                continue
            if bh > bw * 1.25 and bw < est_w:
                continue
            d = (bx - cx) ** 2 + (by - cy) ** 2
            if d < best_d:
                best_d = d
                best_i = i
        if best_i is None:
            out.append((lab, cx, cy, w, h))
            continue
        used.add(best_i)
        snapped[li] = True
        bx, by, bw, bh = blobs[best_i]
        out.append((lab, *_clamp_xy(bx, by), bw, bh))
    refined: list[tuple[str, float, float, float, float]] = []
    for i, (lab, cx, cy, w, h) in enumerate(out):
        if snapped[i]:
            refined.append((lab, cx, cy, w, h))
            continue
        est_w = max(48.0, len(_norm_label(lab)) * 22.0)
        ink = _local_ink_box(im, lab, cx, cy, est_w)
        refined.append(ink if ink else (lab, cx, cy, w, h))
    return refined


def _hand_ellipse(cx: float, cy: float, rx: float, ry: float, n: int = 48) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in range(n):
        a = 2 * math.pi * i / n
        j = 1.0 + 0.025 * math.sin(i * 1.7)
        pts.append((cx + rx * math.cos(a) * j, cy + ry * math.sin(a) * j))
    return pts


def render_circle_mark(out_path: Path, *, rx: int = 78, ry: int = 36) -> tuple[int, int]:
    """手绘黄圈，锚点在圆心。"""
    pad = 18
    w, h = rx * 2 + pad * 2, ry * 2 + pad * 2
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx, cy = w / 2, h / 2
    outer = _hand_ellipse(cx, cy, rx, ry)
    inner = _hand_ellipse(cx, cy, max(8, rx - 7), max(6, ry - 6))
    d.line(outer + [outer[0]], fill=(40, 40, 40, 245), width=8)
    d.line(inner + [inner[0]], fill=(40, 40, 40, 180), width=3)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path, "PNG")
    return int(cx), int(cy)


def render_line_mark(out_path: Path, *, length: int = 200, stroke: int = 12) -> tuple[int, int]:
    """荧光笔划线，几乎拉直，锚点在线的水平中点。"""
    w, h = length + 16, max(24, stroke + 14)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    y = h / 2
    d.line([(8, y), (w - 8, y)], fill=(255, 214, 64, 185), width=stroke)
    d.line([(8, y), (w - 8, y)], fill=(40, 40, 40, 230), width=max(3, stroke // 3))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path, "PNG")
    return w // 2, int(y)


def _draw_spark(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float, fill) -> None:
    pts: list[tuple[float, float]] = []
    for i in range(8):
        ang = math.radians(-90 + i * 45)
        rad = r if i % 2 == 0 else r * 0.36
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    d.polygon(pts, fill=fill)


# 箭头微抖、星光闪（geq 的 N=帧号；overlay 的 alpha= 不能做透明度动画）
POINTER_BOB_X = "2.5*sin(2*PI*t*5)"
POINTER_BOB_Y = "3.5*sin(2*PI*t*4)"


def pointer_output_scale() -> float:
    raw = os.environ.get("AIVIDEO_POINTER_SCALE", "2.2").strip()
    try:
        return max(1.2, min(3.5, float(raw)))
    except ValueError:
        return 2.2


def sparkle_modulate(src: str, dst: str) -> str:
    """星光按帧闪透明度。"""
    return (
        f"[{src}]format=rgba,"
        f"geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
        f"a='alpha(X,Y)*(0.25+0.75*abs(sin(2*PI*N/6)))'[{dst}]"
    )


def render_sparkle(out_path: Path, *, scale: float | None = None) -> tuple[int, int]:
    """闪光星，锚点在中心，叠在箭头尖上闪。"""
    scale = pointer_output_scale() if scale is None else scale
    s = 3
    n = 28 * s
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = n / 2
    d.ellipse([c - 12 * s, c - 12 * s, c + 12 * s, c + 12 * s], fill=(255, 236, 90, 55))
    _draw_spark(d, c, c, 11 * s, (255, 255, 245, 255))
    _draw_spark(d, c, c, 6 * s, (255, 220, 60, 255))
    out_w = max(1, int(round(28 * scale)))
    small = im.resize((out_w, out_w), Image.Resampling.LANCZOS)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    small.save(out_path, "PNG")
    return small.width // 2, small.height // 2


def render_spark_pointer(out_path: Path, *, scale: float | None = None) -> tuple[int, int]:
    """短金箭头 + 星光，尖朝左上。返回笔尖像素。"""
    scale = pointer_output_scale() if scale is None else scale
    s = 3
    W, H = 86 * s, 72 * s
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    tip = (16 * s, 22 * s)
    ang = math.radians(32)

    def xf(lx: float, ly: float) -> tuple[float, float]:
        c, sn = math.cos(ang), math.sin(ang)
        return (tip[0] + lx * c - ly * sn, tip[1] + lx * sn + ly * c)

    d.ellipse(
        [tip[0] - 11 * s, tip[1] - 11 * s, tip[0] + 11 * s, tip[1] + 11 * s],
        fill=(255, 230, 90, 78),
    )
    head = [xf(0, 0), xf(28 * s, -14 * s), xf(18 * s, 0), xf(28 * s, 14 * s)]
    d.polygon(head, fill=(255, 214, 48, 255))
    shaft = [xf(16 * s, -4.5 * s), xf(42 * s, -4.5 * s), xf(42 * s, 4.5 * s), xf(16 * s, 4.5 * s)]
    d.polygon(shaft, fill=(248, 196, 36, 255))
    lw = max(3, int(round(s * scale / 2.2)))
    d.line([xf(0, 0), xf(28 * s, -14 * s)], fill=(62, 48, 28, 230), width=lw)
    d.line([xf(0, 0), xf(28 * s, 14 * s)], fill=(62, 48, 28, 230), width=lw)
    d.line([xf(28 * s, -14 * s), xf(18 * s, 0)], fill=(62, 48, 28, 180), width=max(2, lw - 1))
    d.line([xf(28 * s, 14 * s), xf(18 * s, 0)], fill=(62, 48, 28, 180), width=max(2, lw - 1))
    d.line([xf(20 * s, -2 * s), xf(38 * s, -2 * s)], fill=(255, 245, 170, 210), width=max(2, lw - 1))
    _draw_spark(d, tip[0] - 4 * s, tip[1] - 12 * s, 7 * s, (255, 255, 250, 245))
    _draw_spark(d, tip[0] + 16 * s, tip[1] - 15 * s, 4 * s, (255, 236, 120, 230))
    _draw_spark(d, tip[0] + 8 * s, tip[1] + 14 * s, 3.5 * s, (255, 250, 200, 210))

    out_w = max(1, int(round(W * scale / s)))
    out_h = max(1, int(round(H * scale / s)))
    out = im.resize((out_w, out_h), Image.Resampling.LANCZOS)
    ox, oy = float(tip[0]) * scale / s, float(tip[1]) * scale / s
    bbox = out.getbbox()
    if bbox:
        pad = max(6, int(round(5 * scale)))
        l, t, rgt, b = bbox
        cl, ct = max(0, l - pad), max(0, t - pad)
        out = out.crop((cl, ct, min(out.width, rgt + pad), min(out.height, b + pad)))
        ox -= cl
        oy -= ct
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path, "PNG")
    return int(round(ox)), int(round(oy))


def render_pen_sprite(out_path: Path, *, scale: float | None = None) -> tuple[int, int]:
    """兼容旧名：短闪光箭头。"""
    return render_spark_pointer(out_path, scale=scale)


def overlay_filter(
    *,
    canvas_png: Path,
    labels: list[str],
    phrases: list[str],
    spans: list[tuple[float, float]],
    duration: float,
    work_dir: Path,
    delay_s: float = 0.0,
) -> tuple[str, list[Path]] | None:
    """圈/线贴着文字框；短闪光箭头跟着画。"""
    labels = [str(t).strip() for t in labels if str(t).strip()]
    if not labels:
        return None
    work_dir.mkdir(parents=True, exist_ok=True)
    located = locate_label_points(canvas_png, labels, cache_path=work_dir / "pointer_points.json")
    delay_s = max(0.0, float(delay_s))
    hold_dur = max(0.4, float(duration) - delay_s) if delay_s else float(duration)
    holds = label_holds([p[0] for p in located], phrases, spans, hold_dur)
    if delay_s and holds:
        holds = [(t0 + delay_s, t1 + delay_s, idx) for t0, t1, idx in holds]
    if not holds:
        return None

    ptr_path = work_dir / "lecture_pointer.png"
    spark_path = work_dir / "lecture_sparkle.png"
    tip_x, tip_y = render_spark_pointer(ptr_path)
    render_sparkle(spark_path)

    extra: list[Path] = []
    steps: list[dict] = []
    bits: list[str] = []
    for hi, (t0, t1, idx) in enumerate(holds):
        text, cx, cy, bw, bh = located[idx]
        kind = _mark_kind(text)
        mark = work_dir / f"mark_{hi:02d}.png"
        if kind == "circle":
            rx = int(min(150, max(30, bw / 2 + 16)))
            ry = int(min(72, max(22, bh / 2 + 14)))
            ax, ay = render_circle_mark(mark, rx=rx, ry=ry)
            x = int(round(cx - ax))
            y = int(round(cy - ay))
            steps.append({
                "kind": kind, "t0": t0, "t1": t1, "x": x, "y": y,
                "cx": cx, "cy": cy, "rx": rx, "ry": ry,
            })
        else:
            length = int(min(380, max(48, bw + 12)))
            ax, ay = render_line_mark(mark, length=length)
            x = int(round(cx - ax))
            y = int(round(cy + bh / 2 + 6 - ay))
            steps.append({
                "kind": kind, "t0": t0, "t1": t1, "x": x, "y": y,
                "length": length, "line_y": y + ay,
            })
        extra.append(mark)
        bits.append(f"{'圈' if kind == 'circle' else '划'}「{text}」")

    extra.append(ptr_path)
    extra.append(spark_path)
    n = len(steps)
    ptr_in = n + 2
    spark_in = n + 3
    chains = [
        f"[0:v]scale={CANVAS_W}:{CANVAS_H}:flags=neighbor,fps=30,setsar=1,format=yuv444p[v0]"
    ]
    for hi, st in enumerate(steps):
        inp = hi + 2
        src = f"m{hi}"
        prev = f"v{hi}"
        nxt = f"v{hi + 1}"
        draw = 0.32
        t0, t1 = st["t0"], st["t1"]
        x, y = st["x"], st["y"]
        if st["kind"] == "line":
            wexpr = f"max(2\\,min(iw\\,max(0\\,(n/30-{t0:.3f}))/{draw:.2f}*iw))"
            chains.append(f"[{inp}:v]format=rgba,crop=w='{wexpr}':h=ih:x=0:y=0[{src}]")
        else:
            chains.append(f"[{inp}:v]format=rgba[{src}]")
        chains.append(
            f"[{prev}][{src}]overlay=x={x}:y={y}:enable='between(t\\,{t0:.3f}\\,{t1:.3f})':format=auto[{nxt}]"
        )

    marks_out = f"v{n}"
    chains.append(f"[{ptr_in}:v]format=rgba[ptrsrc]")
    chains.append(sparkle_modulate(f"{spark_in}:v", "spksrc"))
    if n == 1:
        chains.append("[ptrsrc]format=rgba[p0]")
        chains.append("[spksrc]format=rgba[s0]")
    else:
        plabs = "".join(f"[p{i}]" for i in range(n))
        slabs = "".join(f"[s{i}]" for i in range(n))
        chains.append(f"[ptrsrc]split={n}{plabs}")
        chains.append(f"[spksrc]split={n}{slabs}")

    def _tip_xy(st: dict) -> tuple[str, str]:
        t0 = st["t0"]
        draw = 0.32
        if st["kind"] == "line":
            length = float(st["length"])
            x0 = st["x"] + 8
            y_tip = st["line_y"]
            tx = f"{x0:.1f}+min({length:.1f}\\,max(0\\,(t-{t0:.3f})/{draw:.2f}*{length:.1f}))"
            ty = f"{y_tip:.1f}"
            return tx, ty
        cdur = 0.45
        cx, cy, rx, ry = st["cx"], st["cy"], st["rx"], st["ry"]
        ang = f"if(lt(t\\,{t0:.3f}+{cdur:.2f})\\,2*PI*(t-{t0:.3f})/{cdur:.2f}\\,0.70)"
        return f"{cx:.1f}+{rx}*cos({ang})", f"{cy:.1f}+{ry}*sin({ang})"

    for hi, st in enumerate(steps):
        prev = marks_out if hi == 0 else f"a{hi - 1}"
        nxt = f"a{hi}"
        t0, t1 = st["t0"], st["t1"]
        tx, ty = _tip_xy(st)
        chains.append(
            f"[{prev}][p{hi}]overlay=x='{tx}-{tip_x}+{POINTER_BOB_X}':"
            f"y='{ty}-{tip_y}+{POINTER_BOB_Y}':"
            f"enable='between(t\\,{t0:.3f}\\,{t1:.3f})':format=auto[{nxt}]"
        )

    for hi, st in enumerate(steps):
        prev = f"a{n - 1}" if hi == 0 else f"k{hi - 1}"
        nxt = "ann" if hi == n - 1 else f"k{hi}"
        t0, t1 = st["t0"], st["t1"]
        tx, ty = _tip_xy(st)
        chains.append(
            f"[{prev}][s{hi}]overlay=x='{tx}+{POINTER_BOB_X}-W/2':"
            f"y='{ty}+{POINTER_BOB_Y}-H/2':"
            f"enable='between(t\\,{t0:.3f}\\,{t1:.3f})':format=auto[{nxt}]"
        )

    print(f"  [pointer] 闪光箭头：{' '.join(bits)}", flush=True)
    if os.environ.get("AIVIDEO_LECTURE_POINTER_DEBUG", "").strip() in {"1", "true", "yes", "on"}:
        annotate_debug(canvas_png, located, work_dir / "pointer_debug.png")
    return ";".join(chains), extra


def annotate_debug(
    canvas_png: Path,
    located: list[tuple[str, float, float, float, float]],
    out_path: Path,
) -> None:
    im = Image.open(canvas_png).convert("RGB")
    d = ImageDraw.Draw(im)
    for i, (text, x, y, bw, bh) in enumerate(located, start=1):
        d.rectangle((x - bw / 2, y - bh / 2, x + bw / 2, y + bh / 2), outline=(220, 40, 40), width=3)
        d.text((x - bw / 2, y - bh / 2 - 22), f"{i}:{text}", fill=(220, 40, 40))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path, "PNG")
