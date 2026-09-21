"""快切版（成片 50-60s）合成组件。

- 两拍冷开场：第 1 帧满屏数字对比（= 封面）→ 物体示意图 + 两行大字钩子
- 每页「错觉 → 黄笔叉掉 → 真相」卡片仪式（固定噱头，替代真人主播）
- 每个要点镜头推进（复用 lecture_pointer 的包围盒）
- 含数字的要点弹出大号数字

AIVIDEO_FAST_CUT=0 关闭，脚本无 hook_number / myth 时自动回退旧版。
PIL 绘制函数在调用时才 import video_compose，避免循环导入。
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw

INK = (40, 40, 40)
PAPER = (255, 255, 255)
HIGHLIGHT_YELLOW = (255, 221, 51, 215)

_NUM_TOKEN = re.compile(
    r"[-+]?\d[\d.,]*\s*(?:%|％|亿|万|倍|个月|个点|年|天|元|块|bp|BP|折|成|次|家|台)?"
)


def enabled() -> bool:
    from research import fast_cut_enabled

    return fast_cut_enabled()


def hook_lines(cold_open: str) -> list[str]:
    """冷开场拆两小句：前句场景、后句爆点。"""
    parts = [p.strip() for p in re.split(r"[，,]", cold_open or "") if p.strip()]
    if len(parts) >= 2:
        return [parts[0], "，".join(parts[1:])]
    text = (cold_open or "").strip()
    if len(text) <= 10:
        return [text] if text else []
    mid = len(text) // 2
    return [text[:mid], text[mid:]]


def ritual_duration(clip_duration: float) -> float:
    """错觉卡停留时长：短页也留够看清「叉掉」的时间。"""
    return min(2.8, max(1.6, float(clip_duration) * 0.30))


def _bbox(font, text: str) -> tuple[int, int, int, int]:
    return font.getbbox(text)


def _text_size(font, text: str) -> tuple[int, int]:
    b = _bbox(font, text)
    return b[2] - b[0], b[3] - b[1]


# ------------------------------------------------------------
# 冷开场两拍
# ------------------------------------------------------------
def render_hook_number_card(*, hook_number: str, cold_open: str, out_path: Path) -> Path:
    """第 1 帧 / 封面：满屏数字对比 + 两行钩子大字。"""
    import video_compose as vc

    W, H = vc.CANVAS_W, vc.CANVAS_H
    canvas = Image.new("RGB", (W, H), vc.BG_COLOR)
    d = ImageDraw.Draw(canvas)
    vc._draw_grid(d)
    accent = vc._accent()

    num = (hook_number or "").strip()
    size = vc._fit_font_size(num, W - 140, base_size=300, min_size=110)
    f = vc.load_font(size)
    b = _bbox(f, num)
    tw, th = b[2] - b[0], b[3] - b[1]
    nx = (W - tw) // 2
    ny = 500
    d.rectangle([(nx - 30, ny + int(th * 0.52)), (nx + tw + 30, ny + th + 34)], fill=accent)
    d.text((nx - b[0], ny - b[1]), num, font=f, fill=INK)

    lines = hook_lines(cold_open)
    y = ny + th + 130
    if lines:
        s1 = lines[0]
        f1 = vc.load_font(vc._fit_font_size(s1, W - 200, base_size=104, min_size=60))
        b1 = _bbox(f1, s1)
        d.text((((W - (b1[2] - b1[0])) // 2) - b1[0], y - b1[1]), s1, font=f1, fill=INK)
        y += (b1[3] - b1[1]) + 64
    if len(lines) > 1:
        s2 = lines[1]
        f2 = vc.load_font(vc._fit_font_size(s2, W - 280, base_size=126, min_size=68))
        b2 = _bbox(f2, s2)
        tw2, th2 = b2[2] - b2[0], b2[3] - b2[1]
        pad_x, pad_y = 46, 34
        bx1 = (W - tw2) // 2 - pad_x
        bx2 = bx1 + tw2 + 2 * pad_x
        by1 = y - pad_y
        by2 = y + th2 + pad_y
        d.rounded_rectangle([(bx1 + 14, by1 + 14), (bx2 + 14, by2 + 14)], radius=36, fill=INK)
        d.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=36, fill=PAPER, outline=INK, width=6)
        d.text((bx1 + pad_x - b2[0], y - b2[1]), s2, font=f2, fill=INK)

    vc._draw_brand_badge(d, font_size=50)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


def render_hook_hero_frame(*, cold_open: str, hero_image: Path | None, out_path: Path) -> Path:
    """第 2 拍：两行钩子大字在上，物体示意图在下。"""
    import video_compose as vc

    W, H = vc.CANVAS_W, vc.CANVAS_H
    canvas = Image.new("RGB", (W, H), vc.BG_COLOR)
    d = ImageDraw.Draw(canvas)
    vc._draw_grid(d)

    lines = hook_lines(cold_open)
    y = 250
    if lines:
        s1 = lines[0]
        f1 = vc.load_font(vc._fit_font_size(s1, W - 200, base_size=88, min_size=54))
        b1 = _bbox(f1, s1)
        d.text((((W - (b1[2] - b1[0])) // 2) - b1[0], y - b1[1]), s1, font=f1, fill=INK)
        y += (b1[3] - b1[1]) + 44
    if len(lines) > 1:
        s2 = lines[1]
        f2 = vc.load_font(vc._fit_font_size(s2, W - 240, base_size=112, min_size=62))
        b2 = _bbox(f2, s2)
        tw2, th2 = b2[2] - b2[0], b2[3] - b2[1]
        pad_x, pad_y = 40, 28
        bx1 = (W - tw2) // 2 - pad_x
        bx2 = bx1 + tw2 + 2 * pad_x
        by1 = y - pad_y
        by2 = y + th2 + pad_y
        d.rounded_rectangle([(bx1 + 12, by1 + 12), (bx2 + 12, by2 + 12)], radius=32, fill=INK)
        d.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=32, fill=PAPER, outline=INK, width=6)
        d.text((bx1 + pad_x - b2[0], y - b2[1]), s2, font=f2, fill=INK)
        y = by2

    top = y + 56
    if hero_image and Path(hero_image).is_file():
        with Image.open(hero_image) as src:
            img = src.convert("RGB")
        max_w = W - 140
        max_h = H - top - 90
        ratio = min(max_w / img.width, max_h / img.height)
        nw, nh = int(img.width * ratio), int(img.height * ratio)
        img = img.resize((nw, nh), Image.LANCZOS)
        x = (W - nw) // 2
        canvas.paste(img, (x, top))
        img.close()
        d.rectangle([(x - 6, top - 6), (x + nw + 6, top + nh + 6)], outline=INK, width=4)
    else:
        vc._draw_corner_doodles(d)

    vc._draw_brand_badge(d, font_size=50)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


# ------------------------------------------------------------
# 每页错觉卡：以为 → 叉掉 → 其实
# ------------------------------------------------------------
def render_myth_assets(
    *,
    myth: str,
    truth: str,
    work_dir: Path,
    ritual_end: float,
) -> list[dict]:
    """返回 HUD 叠层列表 [{path, x, y, t0, t1}]，屏幕坐标（叠在推镜之后）。"""
    import video_compose as vc

    W = vc.CANVAS_W
    accent = vc._accent()
    work_dir.mkdir(parents=True, exist_ok=True)

    myth_text = f"以为：{(myth or '').strip()}"
    truth_text = f"其实：{(truth or '').strip()}"
    card_w = W - 120
    inner_w = card_w - 100
    f1 = vc.load_font(vc._fit_font_size(myth_text, inner_w, base_size=72, min_size=44))
    f2 = vc.load_font(vc._fit_font_size(truth_text, inner_w, base_size=80, min_size=48))
    b1 = _bbox(f1, myth_text)
    b2 = _bbox(f2, truth_text)
    w1, h1 = b1[2] - b1[0], b1[3] - b1[1]
    w2, h2 = b2[2] - b2[0], b2[3] - b2[1]
    pad = 36
    gap = 30
    card_h = pad + h1 + gap + h2 + pad
    shadow = 12

    card = Image.new("RGBA", (card_w + shadow + 4, card_h + shadow + 4), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle([(shadow, shadow), (card_w + shadow, card_h + shadow)], radius=30, fill=INK + (255,))
    cd.rounded_rectangle([(0, 0), (card_w, card_h)], radius=30, fill=PAPER + (245,), outline=INK + (255,), width=6)
    myth_x = (card_w - w1) // 2
    myth_y = pad
    cd.text((myth_x - b1[0], myth_y - b1[1]), myth_text, font=f1, fill=(90, 90, 90, 255))
    card_path = work_dir / "myth_card.png"
    card.save(card_path, "PNG")

    card_x, card_y = 60, 240

    # 黄荧光笔叉：盖住「以为」整行
    xw, xh = w1 + 70, h1 + 50
    xmark = Image.new("RGBA", (xw, xh), (0, 0, 0, 0))
    xd = ImageDraw.Draw(xmark)
    stroke = max(16, int(xh * 0.28))
    xd.line([(8, xh - 10), (xw - 8, 10)], fill=HIGHLIGHT_YELLOW, width=stroke)
    xd.line([(8, 10), (xw - 8, xh - 10)], fill=HIGHLIGHT_YELLOW, width=stroke)
    x_path = work_dir / "myth_x.png"
    xmark.save(x_path, "PNG")
    x_x = card_x + myth_x - 35
    x_y = card_y + myth_y - 25

    # 真相：主题色高亮带 + 黑字
    tw_pad = 18
    truth_img = Image.new("RGBA", (w2 + tw_pad * 2, h2 + 30), (0, 0, 0, 0))
    td = ImageDraw.Draw(truth_img)
    td.rectangle([(0, int(h2 * 0.5) + 10), (w2 + tw_pad * 2, h2 + 28)], fill=accent + (255,))
    td.text((tw_pad - b2[0], 10 - b2[1]), truth_text, font=f2, fill=INK + (255,))
    truth_path = work_dir / "myth_truth.png"
    truth_img.save(truth_path, "PNG")
    truth_x = card_x + (card_w - (w2 + tw_pad * 2)) // 2
    truth_y = card_y + pad + h1 + gap - 10

    t_x = 0.65
    t_truth = 1.10
    end = max(ritual_end, t_truth + 0.6)
    return [
        {"path": card_path, "x": card_x, "y": card_y, "t0": 0.0, "t1": end},
        {"path": x_path, "x": x_x, "y": x_y, "t0": t_x, "t1": end},
        {"path": truth_path, "x": truth_x, "y": truth_y, "t0": t_truth, "t1": end},
    ]


# ------------------------------------------------------------
# 数字弹出
# ------------------------------------------------------------
def number_token(label: str) -> str | None:
    label = (label or "").strip()
    if not re.search(r"\d", label):
        return None
    if len(label) <= 7:
        return label
    m = _NUM_TOKEN.search(label)
    return m.group(0).strip() if m else None


def render_number_pop(text: str, out_path: Path, *, scale: float = 1.0) -> tuple[Path, int, int]:
    import video_compose as vc

    W = vc.CANVAS_W
    accent = vc._accent()
    size = vc._fit_font_size(text, int(880 * scale), base_size=int(190 * scale), min_size=int(80 * scale))
    f = vc.load_font(size)
    b = _bbox(f, text)
    tw, th = b[2] - b[0], b[3] - b[1]
    pad_x, pad_y = 48, 30
    img_w, img_h = tw + pad_x * 2 + 16, th + pad_y * 2 + 16
    img = Image.new("RGBA", (img_w, img_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([(12, 12), (img_w - 4, img_h - 4)], radius=34, fill=INK + (255,))
    d.rounded_rectangle([(0, 0), (img_w - 16, img_h - 16)], radius=34, fill=PAPER + (240,), outline=INK + (255,), width=6)
    d.text((pad_x - b[0], pad_y - b[1]), text, font=f, fill=accent + (255,), stroke_width=3, stroke_fill=INK + (255,))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    x = (W - img_w) // 2
    return out_path, x, img_w


def number_pops(steps: list[dict], work_dir: Path, *, y: int = 330, show_s: float = 1.05) -> list[dict]:
    """含数字的要点在推镜开始时弹出大号数字。每页最多 2 个，避免 HUD 层数过多。"""
    items: list[dict] = []
    for i, st in enumerate(steps):
        if len(items) >= 2:
            break
        tok = number_token(str(st.get("label") or ""))
        if not tok:
            continue
        t0 = float(st["t0"])
        t1 = min(float(st["t1"]), t0 + show_s)
        if t1 - t0 < 0.5:
            continue
        big, bx, _ = render_number_pop(tok, work_dir / f"pop_{i:02d}.png", scale=1.0)
        items.append({"path": big, "x": bx, "y": y, "t0": t0, "t1": t1})
    return items


# ------------------------------------------------------------
# ffmpeg 滤镜片段
# ------------------------------------------------------------
def zoom_punch_filter(steps: list[dict], *, zoom: float = 1.28) -> str | None:
    """按要点时段把镜头推到该要点中心。用 crop+scale，不用 zoompan（省内存、不卡首帧）。"""
    import video_compose as vc

    pulses: list[str] = []
    cxs: list[str] = []
    cys: list[str] = []
    actives: list[str] = []
    attack, release = 0.28, 0.22
    for st in steps:
        t0, t1 = float(st["t0"]), float(st["t1"])
        if t1 - t0 < 0.9:
            continue
        cx = float(st.get("cx", vc.CANVAS_W / 2))
        cy = float(st.get("cy", vc.CANVAS_H / 2))
        pulses.append(
            f"clip((t-{t0:.3f})/{attack}\\,0\\,1)*clip(({t1:.3f}-t)/{release}\\,0\\,1)"
        )
        cxs.append(f"{cx:.1f}*between(t\\,{t0:.3f}\\,{t1:.3f})")
        cys.append(f"{cy:.1f}*between(t\\,{t0:.3f}\\,{t1:.3f})")
        actives.append(f"between(t\\,{t0:.3f}\\,{t1:.3f})")
    if not pulses:
        return None
    z = f"(1+{zoom - 1:.3f}*({'+'.join(pulses)}))"
    w = f"trunc(iw/{z}/2)*2"
    h = f"trunc(ih/{z}/2)*2"
    cx_e = f"if({'+'.join(actives)}\\,{'+'.join(cxs)}\\,{vc.CANVAS_W / 2:.1f})"
    cy_e = f"if({'+'.join(actives)}\\,{'+'.join(cys)}\\,{vc.CANVAS_H / 2:.1f})"
    x = f"clip(({cx_e})-({w})/2\\,0\\,iw-({w}))"
    y = f"clip(({cy_e})-({h})/2\\,0\\,ih-({h}))"
    return (
        f"crop=w='{w}':h='{h}':x='{x}':y='{y}',"
        f"scale={vc.CANVAS_W}:{vc.CANVAS_H}:flags=fast_bilinear"
    )


def build_hud_sheet(items: list[dict], out_path: Path) -> Path | None:
    """所有 HUD 图层竖向拼成一张精灵图，只占 ffmpeg 一个输入。

    ffmpeg 7.x 上 -loop 1 图片输入超过 ~9 个会在 filter_complex 里死锁，
    所以 HUD 层不能一层一个输入。就地给每个 item 写入 sheet_x/sheet_y/w/h。
    """
    if not items:
        return None
    imgs: list[Image.Image] = []
    try:
        for it in items:
            with Image.open(it["path"]) as src:
                imgs.append(src.convert("RGBA"))
        gap = 4
        sheet_w = max(im.width for im in imgs)
        sheet_h = sum(im.height for im in imgs) + gap * (len(imgs) - 1)
        sheet = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))
        y = 0
        for it, im in zip(items, imgs):
            sheet.paste(im, (0, y))
            it["sheet_x"], it["sheet_y"], it["w"], it["h"] = 0, y, im.width, im.height
            y += im.height + gap
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out_path, "PNG")
        sheet.close()
        return out_path
    finally:
        for im in imgs:
            try:
                im.close()
            except Exception:
                pass


def hud_overlays(items: list[dict], *, sheet_input_index: int, in_label: str, out_label: str) -> list[str]:
    """从精灵图裁出每个 HUD 层，按时间叠到 in_label 上，最终输出 out_label。"""
    if not items:
        return [f"[{in_label}]null[{out_label}]"]
    n = len(items)
    chains: list[str] = []
    if n == 1:
        chains.append(f"[{sheet_input_index}:v]format=rgba[q0]")
    else:
        outs = "".join(f"[q{k}]" for k in range(n))
        chains.append(f"[{sheet_input_index}:v]format=rgba,split={n}{outs}")
    prev = in_label
    for k, it in enumerate(items):
        src = f"hud{k}"
        chains.append(
            f"[q{k}]crop={int(it['w'])}:{int(it['h'])}:{int(it['sheet_x'])}:{int(it['sheet_y'])}[{src}]"
        )
        nxt = out_label if k == n - 1 else f"h{k}"
        chains.append(
            f"[{prev}][{src}]overlay=x={int(it['x'])}:y={int(it['y'])}:"
            f"enable='between(t\\,{float(it['t0']):.3f}\\,{float(it['t1']):.3f})':format=yuv420[{nxt}]"
        )
        prev = nxt
    return chains
