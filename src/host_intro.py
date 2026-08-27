"""B 方案片头：按话题生成怪异主持人 + 笔记本标题；不露嘴，避免对口型。"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance

from image_client import comic_style_prefix, generate_image, save_b64_image
from paths import ROOT, ffmpeg_executable
from tts_client import synthesize as tts_synthesize
from video_compose import (
    CANVAS_W,
    CANVAS_H,
    TTS_SAMPLE_RATE,
    _draw_brand_badge,
    ffprobe_duration,
    set_theme,
)

WAN_MODEL = "wan2.6-i2v-flash"
WAN_SUBMIT = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"


# 每期换皮：只改物体形态，不给人脸、不戴口罩。
_BODY_TWISTS = (
    "tiny stubby legs under a huge object-torso",
    "one oversized pointing arm, the other arm a gadget/prop",
    "a hat or flag planted on top of the object, no face below it",
    "lit rectangular windows as architecture, not cartoon eyeballs",
    "the object leans like a person but stays an object — no human skin",
    "cables / ticker tape / vines as hair, still no mouth",
    "wearing a tiny jacket on an object body, collar does not hide a face because there is no face",
    "the 'head' is just the top of the object (roof, lid, coin edge) — blank, no features except optional eyes",
)

_TOPIC_PUNS = (
    (r"美联储|转鹰|加息|降息|沃什|FOMC", "a ballpoint-pen doodle of a tiny Fed building with stick arms, or a dollar-bill origami hawk"),
    (r"房贷|房价|买房|月供", "a ballpoint-pen doodle of a walking apartment block, or a sofa with arms"),
    (r"基金|理财|净值", "a ballpoint-pen doodle of a piggy-bank golem, or a walking candlestick chart"),
    (r"黄金|金价|金饰", "a ballpoint-pen doodle of a gold-bar character, or a gilded koi with fin-arms"),
    (r"原油|油价|OPEC", "a ballpoint-pen doodle of an oil-drum knight with a hose cape"),
    (r"芯片|半导体|英伟达|海力士|台积电", "a ballpoint-pen doodle of a GPU tower with cable-hair, or a wafer knight"),
    (r"苹果|iPhone|手机", "a ballpoint-pen doodle of an apple-gadget body with cable arms"),
    (r"人民币|美元|汇率", "a ballpoint-pen doodle of a coin-stack creature, or an origami money crane"),
    (r"AI|大模型|ChatGPT", "a ballpoint-pen doodle of a CRT monitor with legs, or a robot made of receipts"),
    (r"通胀|CPI|物价|买菜", "a ballpoint-pen doodle of a grocery-bag yeti, or a shopping-cart creature"),
)


def _host_seed(title: str) -> int:
    return int(hashlib.sha1((title or "").encode("utf-8")).hexdigest()[:8], 16)


def _body_twist(title: str) -> str:
    return _BODY_TWISTS[_host_seed(title) % len(_BODY_TWISTS)]


def _topic_pun(title: str, hint: str = "") -> str:
    blob = f"{title} {hint}"
    hits = [desc for pat, desc in _TOPIC_PUNS if re.search(pat, blob, re.I)]
    if hits:
        return hits[0]
    return (
        "one surreal OBJECT mascot that puns the Chinese title "
        "(building, appliance, food, furniture, animal-object) — not a human, not an influencer"
    )


def _still_prompt(title: str, *, hint: str = "") -> str:
    title = (title or "").strip()
    twist = _body_twist(title)
    pun = _topic_pun(title, hint)
    return " ".join(
        [
            comic_style_prefix(),
            "Important safe area for Douyin/TikTok UI: keep all meaningful text away from the top 18% of the canvas, "
            "the leftmost 8%, the rightmost 12%, and the bottom 25%.",
            f"Scene: LEFT half a WEIRD NON-HUMAN object-creature mascot inspired by 「{title}」: {pun}.",
            f"Body twist for this episode: {twist}.",
            "Draw it in the SAME black ballpoint pen + beige graph paper style as the later explainer pages.",
            "Simple doodle arms so it can point. It is NOT a person.",
            "ABSOLUTE — faceless object, never a person: no human face, no cartoon face, no cartoon eyeballs, "
            "no lips, no teeth, no tongue, no jaw, no smile. "
            "NO surgical mask, NO cloth mask, NO N95, NO paper bag over a face. "
            "Windows stay as drawn rectangles, not googly eyes. A tiny flag or hat on top is fine.",
            "The mascot points a right-hand index finger (five fingers only) at a giant notebook page filling the RIGHT half.",
            f"The notebook page has a bold handwritten Chinese title EXACTLY: 「{title}」 "
            "plus a small yellow highlighter underline and a tiny question-mark doodle. No other text on the notebook.",
            "Bottom 22% of the canvas must be left as clean empty graph paper (no drawing, no text). "
            "Top-left 12% empty graph paper for a brand badge.",
            "No extra people, no logos, no watermarks, no phone UI, no frames, no photographic elements.",
        ]
    )


def _match_body_paper(img: Image.Image) -> Image.Image:
    """往正文页暖米色方格纸靠一点，避免片头偏 3D 暖光。"""
    im = img.convert("RGB")
    im = ImageEnhance.Color(im).enhance(0.78)
    paper = Image.new("RGB", im.size, (251, 246, 228))
    return Image.blend(im, paper, 0.12)


def _to_canvas(src: Path, dest: Path) -> Path:
    img = _match_body_paper(Image.open(src).convert("RGB"))
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), (251, 246, 228))
    ratio = min(CANVAS_W / img.width, CANVAS_H / img.height)
    nw, nh = int(img.width * ratio), int(img.height * ratio)
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas.paste(img, ((CANVAS_W - nw) // 2, (CANVAS_H - nh) // 2))
    _draw_brand_badge(ImageDraw.Draw(canvas), font_size=54)
    dest.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(dest, "PNG")
    return dest


def _jpeg_data_uri(png: Path) -> str:
    jpg = png.with_suffix(".jpg")
    subprocess.check_call(
        [ffmpeg_executable(), "-y", "-i", str(png), "-q:v", "3", str(jpg)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return "data:image/jpeg;base64," + base64.b64encode(jpg.read_bytes()).decode()


def _pad_audio_min_duration(src: Path, *, min_s: float = 3.2) -> Path:
    """万相对口型要求音频 3–30s；口播不够则尾部垫静音。"""
    dur = ffprobe_duration(src)
    if dur >= min_s:
        return src
    out = src.with_name(src.stem + "_pad.mp3")
    cmd = [
        ffmpeg_executable(), "-y", "-i", str(src),
        "-af", f"apad=pad_dur={min_s - dur:.3f}",
        "-t", f"{min_s:.3f}",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-800:])
    return out


def _audio_data_uri(audio_path: Path) -> str:
    """万相 audio_url 兜底：data URI（实测可用，payload 较大）。"""
    suffix = audio_path.suffix.lower()
    mime = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
    }.get(suffix, "audio/mpeg")
    b64 = base64.b64encode(audio_path.read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def _audio_url_for_wan(audio_path: Path) -> str:
    """优先走 english-test 同款正式 OSS 签名 URL，失败再退 data URI。"""
    try:
        from oss_client import oss_configured, upload_host_intro_audio

        if oss_configured():
            obj = upload_host_intro_audio(audio_path)
            print(f"  [host-intro] 口播已上传 OSS: {obj.key}", flush=True)
            return obj.url
    except Exception as exc:  # noqa: BLE001
        print(f"  [host-intro] OSS 上传失败，改用 data URI: {exc}", flush=True)
    return _audio_data_uri(audio_path)


def _is_paper_pixel(r: int, g: int, b: int) -> bool:
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    return luma >= 208 and b >= 168 and abs(r - g) < 42


def _find_notebook_split_x(img: Image.Image) -> int:
    """线圈是一条又高又黑的竖条；切在它左边，避免本和字被带走。"""
    w, h = img.size
    rgb = img.convert("RGB")
    px = rgb.load()
    best_x, best_frac = 0, 0.0
    x0, x1 = int(w * 0.30), int(w * 0.58)
    for x in range(x0, x1):
        dark = 0
        n = 0
        for y in range(380, min(h - 80, 1450), 4):
            r, g, b = px[x, y]
            n += 1
            if 0.299 * r + 0.587 * g + 0.114 * b < 90:
                dark += 1
        frac = dark / max(1, n)
        if frac > best_frac:
            best_frac, best_x = frac, x
    if best_frac >= 0.28:
        return max(360, best_x - 10)
    for x in range(int(w * 0.38), int(w * 0.70)):
        acc = 0
        n = 0
        for y in range(280, 1400, 10):
            r, g, b = px[x, y]
            acc += 0.299 * r + 0.587 * g + 0.114 * b
            n += 1
        if n and acc / n > 188:
            return max(360, x - 8)
    return min(w - 80, max(420, int(w * 0.46)))


def _knockout_paper(sprite: Image.Image) -> Image.Image:
    sprite = sprite.copy()
    px = sprite.load()
    w, h = sprite.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a and _is_paper_pixel(r, g, b):
                px[x, y] = (r, g, b, 0)
    return sprite


def _erase_mascot_on_bg(img: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    """把小人占过的像素换成纸色，避免跳动时底下露出静帧重影。"""
    out = img.copy()
    px = out.load()
    left, top, right, bot = box
    paper = (245, 238, 220, 255)
    for y in range(max(0, top - 24), top + 36):
        for x in range(left, min(right, out.width), 6):
            r, g, b, a = px[x, y]
            if _is_paper_pixel(r, g, b):
                paper = (r, g, b, 255)
                break
        else:
            continue
        break
    for y in range(top, bot):
        for x in range(left, right):
            r, g, b, a = px[x, y]
            if not _is_paper_pixel(r, g, b):
                px[x, y] = paper
    return out


def _extract_mascot_layers(still: Path, sprite_path: Path, bg_path: Path) -> tuple[int, int]:
    """硬切左侧吉祥物；本和字留在背景上完全静止。返回叠回位置 (x, y)。"""
    img = Image.open(still).convert("RGBA")
    w, h = img.size
    split_x = min(w - 60, _find_notebook_split_x(img))
    top = 240  # 徽标以下，避免角标跟着跳
    bot = min(h - 40, 1520)
    box = (0, top, split_x, bot)
    sprite = _knockout_paper(img.crop(box))
    bg = _erase_mascot_on_bg(img, box)
    sprite_path.parent.mkdir(parents=True, exist_ok=True)
    sprite.save(sprite_path, "PNG")
    bg.save(bg_path, "PNG")
    return 0, top


def _is_yellow_mark(r: int, g: int, b: int) -> bool:
    return r > 190 and g > 155 and b < 135 and (r - b) > 55


def _find_intro_underline(img: Image.Image) -> tuple[int, int, int] | None:
    """笔记本标题下的黄线：(x0, x1, y)。"""
    rgb = img.convert("RGB")
    px = rgb.load()
    w, h = rgb.size
    best: tuple[int, int, int, int] | None = None  # count, y, x0, x1
    x_lo = int(w * 0.42)
    for y in range(420, min(h - 80, 1100)):
        xs: list[int] = []
        for x in range(x_lo, w - 12):
            if _is_yellow_mark(*px[x, y]):
                xs.append(x)
        if len(xs) < 50:
            continue
        x0, x1 = min(xs), max(xs)
        if x1 - x0 < 200:
            continue
        score = len(xs)
        if best is None or score > best[0]:
            best = (score, y, x0, x1)
    if not best:
        return None
    _count, y, x0, x1 = best
    return x0, x1, y


def _cover_intro_underline(img: Image.Image, x0: int, x1: int, y: int, thick: int = 34) -> Image.Image:
    """用附近纸色盖掉静帧上已经画好的黄线，改由铅笔现场划。"""
    out = img.convert("RGBA")
    px = out.load()
    w, h = out.size
    paper = (245, 238, 220, 255)
    sample_y = min(h - 2, y + thick + 10)
    for x in range(max(0, x0), min(w, x1 + 1), 4):
        r, g, b, a = px[x, sample_y]
        luma = 0.299 * r + 0.587 * g + 0.114 * b
        if luma > 190:
            paper = (r, g, b, 255)
            break
    top, bot = max(0, y - thick // 2), min(h, y + thick // 2 + 4)
    left, right = max(0, x0 - 12), min(w, x1 + 16)
    for yy in range(top, bot):
        for xx in range(left, right):
            r, g, b, a = px[xx, yy]
            if _is_yellow_mark(r, g, b):
                px[xx, yy] = paper
    return out


def _intro_puppet_move(still: Path, duration_s: float, out: Path) -> Path:
    """左侧小人点头；右边本和字静止；闪光短箭头把标题下的线划出来。"""
    from lecture_pointer import (
        POINTER_BOB_X,
        POINTER_BOB_Y,
        render_line_mark,
        render_spark_pointer,
        render_sparkle,
        sparkle_modulate,
    )

    duration_s = max(2.2, float(duration_s))
    sprite = still.with_name("intro_mascot_sprite.png")
    bg = still.with_name("intro_puppet_bg.png")
    ox, oy = _extract_mascot_layers(still, sprite, bg)
    bg_im = Image.open(bg).convert("RGBA")
    underline = _find_intro_underline(bg_im)
    ptr_path = still.with_name("intro_spark_pointer.png")
    spark_path = still.with_name("intro_sparkle.png")
    line_path = still.with_name("intro_title_line.png")
    tip_x, tip_y = render_spark_pointer(ptr_path)
    render_sparkle(spark_path)

    y_expr = f"{oy}+6*gte(sin(2*PI*t/1.25)\\,0)"
    fc = (
        f"[0:v]scale={CANVAS_W}:{CANVAS_H}:flags=neighbor,setsar=1,fps=30,format=yuv444p[bg];"
        f"[1:v]format=rgba[ch];"
        f"[bg][ch]overlay=x={ox}:y='{y_expr}':format=auto[scene]"
    )
    cmd = [
        ffmpeg_executable(), "-y",
        "-loop", "1", "-framerate", "30", "-i", str(bg),
        "-loop", "1", "-framerate", "30", "-i", str(sprite),
    ]
    if underline:
        x0, x1, ly = underline
        bg_im = _cover_intro_underline(bg_im, x0, x1, ly)
        bg_im.save(bg, "PNG")
        length = max(80, x1 - x0)
        ax, ay = render_line_mark(line_path, length=length, stroke=18)
        lx = int(x0 - (ax - length // 2) - 8)
        ly_ov = int(ly - ay)
        draw_t = min(1.35, max(0.7, duration_s * 0.32))
        t0 = 0.28
        cmd += ["-loop", "1", "-framerate", "30", "-i", str(line_path)]
        cmd += ["-loop", "1", "-framerate", "30", "-i", str(ptr_path)]
        cmd += ["-loop", "1", "-framerate", "30", "-i", str(spark_path)]
        wexpr = f"max(2\\,min(iw\\,max(0\\,(n/30-{t0:.2f}))/{draw_t:.2f}*iw))"
        tip = (
            f"{x0:.1f}+min({length:.1f}\\,max(0\\,(t-{t0:.2f})/{draw_t:.2f}*{length:.1f}))"
        )
        fc += (
            f";[2:v]format=rgba,crop=w='{wexpr}':h=ih:x=0:y=0[ln]"
            f";[scene][ln]overlay=x={lx}:y={ly_ov}:enable='gte(t\\,{t0:.2f})':format=auto[marked]"
            f";[3:v]format=rgba[ptr]"
            f";[marked][ptr]overlay=x='{tip}-{tip_x}+{POINTER_BOB_X}':"
            f"y='{ly:.1f}-{tip_y}+{POINTER_BOB_Y}':"
            f"enable='gte(t\\,{t0:.2f})':format=auto[aimed]"
            f";{sparkle_modulate('4:v', 'spk')}"
            f";[aimed][spk]overlay=x='{tip}+{POINTER_BOB_X}-W/2':"
            f"y='{ly:.1f}+{POINTER_BOB_Y}-H/2':"
            f"enable='gte(t\\,{t0:.2f})':format=auto,format=yuv420p[vout]"
        )
        print(f"  [host-intro] 闪光箭头划标题 {x0}→{x1} y={ly}", flush=True)
    else:
        cmd += ["-loop", "1", "-framerate", "30", "-i", str(ptr_path)]
        cmd += ["-loop", "1", "-framerate", "30", "-i", str(spark_path)]
        px = int(CANVAS_W * 0.62)
        py = int(CANVAS_H * 0.38)
        fc += (
            f";[2:v]format=rgba[ptr]"
            f";[scene][ptr]overlay=x='{px - tip_x}+{POINTER_BOB_X}':"
            f"y='{py - tip_y}+{POINTER_BOB_Y}':format=auto[aimed]"
            f";{sparkle_modulate('3:v', 'spk')}"
            f";[aimed][spk]overlay=x='{px}+{POINTER_BOB_X}-W/2':"
            f"y='{py}+{POINTER_BOB_Y}-H/2':format=auto,format=yuv420p[vout]"
        )
        print("  [host-intro] 未找到标题黄线，箭头停在本上", flush=True)

    cmd += [
        "-t", f"{duration_s:.3f}",
        "-filter_complex", fc,
        "-map", "[vout]",
        "-an",
        "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        simple = fc.replace(sparkle_modulate("4:v", "spk"), "[4:v]format=rgba[spk]")
        simple = simple.replace(sparkle_modulate("3:v", "spk"), "[3:v]format=rgba[spk]")
        if simple != fc:
            cmd[cmd.index("-filter_complex") + 1] = simple
            proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[-1500:])
    return out


def _wan_i2v_enabled() -> bool:
    return os.environ.get("AIVIDEO_HOST_INTRO_I2V", "0").strip().lower() in {"1", "true", "yes", "on"}


def _wan_i2v(
    img_png: Path,
    out_mp4: Path,
    *,
    duration_s: int,
    audio_path: Path | None = None,
) -> Path:
    key = (os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY")
    duration_s = max(2, min(15, int(duration_s)))
    prompt = (
        "Vertical 9:16 children's cartoon motion, ONE continuous shot, no cut. "
        "Bring the existing non-human OBJECT mascot to life: its already-drawn stone/object ARM "
        "points more clearly at the notebook, then a small nod and body sway, like a stop-motion cartoon. "
        "The mascot stays a faceless OBJECT — do NOT grow a face, cartoon eyeballs, mouth, lips, teeth, jaw, "
        "or any surgical/cloth mask. Windows stay rectangular lights. A flag may flutter. "
        "Keep EVERY on-screen Chinese character, number, badge and notebook handwriting FROZEN — "
        "do not add, remove, or morph text. No extra people, no burned-in captions, no subtitles, no watermark."
    )
    inp: dict = {
        "prompt": prompt,
        "negative_prompt": (
            "human face, cartoon face, cartoon eyes, mouth, lips, teeth, tongue, jaw, "
            "surgical mask, face mask, n95, kn95, talking, lip sync, "
            "changing text, extra letters, garbled Chinese, watermark, "
            "burned-in subtitle, caption, extra fingers"
        ),
        "img_url": _jpeg_data_uri(img_png),
    }
    params: dict = {
        "resolution": "720P",
        "duration": duration_s,
        "prompt_extend": False,
        "shot_type": "single",
        "watermark": False,
        "audio": True,
    }
    if audio_path and audio_path.is_file():
        inp["audio_url"] = _audio_url_for_wan(audio_path)
        print("  [host-intro] 口播以公网 URL 驱动对口型", flush=True)
    else:
        params["audio"] = False
    body = {
        "model": WAN_MODEL,
        "input": inp,
        "parameters": params,
    }
    req = urllib.request.Request(
        WAN_SUBMIT,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        },
    )
    print(f"  [host-intro] 万相 I2V {duration_s}s …", flush=True)
    with urllib.request.urlopen(req, timeout=180) as resp:
        payload = json.loads(resp.read().decode())
    task_id = (payload.get("output") or {}).get("task_id")
    if not task_id:
        raise RuntimeError(payload)
    t0 = time.time()
    while time.time() - t0 < 240:
        treq = urllib.request.Request(
            f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}",
            headers={"Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(treq, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        out = data.get("output") or {}
        st = out.get("task_status")
        print(f"    {st} {int(time.time() - t0)}s", flush=True)
        if st == "SUCCEEDED":
            urllib.request.urlretrieve(out["video_url"], out_mp4)
            return out_mp4
        if st in {"FAILED", "CANCELED", "UNKNOWN"}:
            raise RuntimeError(out)
        time.sleep(6)
    raise TimeoutError("万相片头超时")


def _has_audio_stream(path: Path) -> bool:
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error", "-select_streams", "a",
                "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path),
            ],
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    return bool(out.strip())


def _canvas_vf() -> str:
    return (
        f"scale={CANVAS_W}:{CANVAS_H}:force_original_aspect_ratio=decrease,"
        f"pad={CANVAS_W}:{CANVAS_H}:(ow-iw)/2:(oh-ih)/2,fps=30,setsar=1,format=yuv420p"
    )


def _cover_hallucinated_captions(video: Path, still: Path, out: Path) -> Path:
    """万相常在底部胡写字幕；用定妆照底栏盖住。"""
    band_h = int(CANVAS_H * 0.34)
    fc = (
        f"[0:v]{_canvas_vf()}[v];"
        f"[1:v]scale={CANVAS_W}:{CANVAS_H},setsar=1,"
        f"crop={CANVAS_W}:{band_h}:0:{CANVAS_H - band_h}[band];"
        f"[v][band]overlay=0:{CANVAS_H - band_h},format=yuv420p[vout]"
    )
    cmd = [
        ffmpeg_executable(), "-y",
        "-i", str(video),
        "-i", str(still),
        "-filter_complex", fc,
        "-map", "[vout]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(TTS_SAMPLE_RATE), "-ac", "2",
        "-movflags", "+faststart",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1500:])
    return out


def _mux_tts(video: Path, audio: Path, out: Path) -> Path:
    vdur = ffprobe_duration(video)
    adur = ffprobe_duration(audio)
    vf = _canvas_vf()
    # 口播长：末帧定格；口播短：裁到口播结束，避免多出来的空嘴型
    if adur > vdur + 0.08:
        extra = adur - vdur
        vf = vf + f",tpad=stop_mode=clone:stop_duration={extra:.3f}"
        dur = adur
    else:
        dur = min(vdur, adur)
    cmd = [
        ffmpeg_executable(), "-y",
        "-i", str(video),
        "-i", str(audio),
        "-filter_complex",
        f"[0:v]{vf}[v];[1:a]apad,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS,"
        f"aformat=sample_fmts=fltp:sample_rates={TTS_SAMPLE_RATE}:channel_layouts=stereo[a]",
        "-map", "[v]", "-map", "[a]",
        "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(TTS_SAMPLE_RATE), "-ac", "2",
        "-movflags", "+faststart",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr[-1500:])
    return out


def generate_host_intro(
    title: str,
    *,
    work_dir: Path,
    category: str | None = "astock",
    reuse_still: bool = False,
    topic_hint: str = "",
) -> Path:
    """生成片头 mp4：每期怪异形象 + 不露嘴；口播后期叠上，不对口型。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    set_theme(category)
    title = (title or "").strip()
    line = f"今天我们要讨论的话题是：{title}"
    audio = work_dir / "host_intro.mp3"
    print(f"  [host-intro] TTS：{line}", flush=True)
    tts_synthesize(line, out_path=audio)
    audio = _pad_audio_min_duration(audio)
    dur = max(3, min(15, math.ceil(ffprobe_duration(audio) - 0.05)))

    still = work_dir / "host_intro_1080.png"
    raw = work_dir / "host_intro_raw.png"
    if reuse_still and still.is_file():
        print(f"  [host-intro] 复用定妆 {still.name}", flush=True)
    else:
        twist = _body_twist(title)
        print(f"  [host-intro] gpt-image-2 生图（无嘴物体：{twist}）", flush=True)
        result = generate_image(_still_prompt(title, hint=topic_hint))
        if result.get("b64_json"):
            save_b64_image(result["b64_json"], raw)
        elif result.get("url"):
            urllib.request.urlretrieve(result["url"], raw)
        else:
            raise RuntimeError(result)
        _to_canvas(raw, still)

    driven = work_dir / "host_intro_driven.mp4"
    out = work_dir / "host_intro.mp4"
    scaled = work_dir / "host_intro_scaled.mp4"
    if _wan_i2v_enabled():
        try:
            print("  [host-intro] 万相让吉祥物动起来（不对口型，避免长嘴）", flush=True)
            _wan_i2v(still, driven, duration_s=dur, audio_path=None)
            _mux_tts(driven, audio, scaled)
            _cover_hallucinated_captions(scaled, still, out)
        except Exception as exc:  # noqa: BLE001
            print(f"  [host-intro] 万相失败，改剪纸动画：{exc}", flush=True)
            _intro_puppet_move(still, ffprobe_duration(audio), driven)
            _mux_tts(driven, audio, out)
    else:
        print("  [host-intro] 剪纸动画：只让左侧吉祥物点头，本和字不动", flush=True)
        _intro_puppet_move(still, ffprobe_duration(audio), driven)
        _mux_tts(driven, audio, out)
    print(f"  [host-intro] 完成 {out} {ffprobe_duration(out):.2f}s", flush=True)
    return out
