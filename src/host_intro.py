"""B 方案片头：方格纸笔记本页 + 数字人指标题，万相 I2V。"""

from __future__ import annotations

import base64
import json
import math
import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

from image_client import generate_image, save_b64_image
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


def _b_prompt(title: str) -> str:
    title = (title or "").strip()
    return f"""Vertical 9:16 still for a Chinese finance explainer.
Background MUST be light beige graph / grid paper like a notebook doodle video.
Warm indoor light. Bottom 22% is EMPTY graph paper for subtitles.
Top-left 12% is EMPTY graph paper so a yellow brand badge can sit there.
Same East Asian man, about 32, short neat black hair, light stubble, light-gray rolled-sleeve shirt, photoreal face.
He stands LEFT, waist-up, pointing his right index finger at a giant notebook page filling the RIGHT.
Five fingers only. No extra people, no logos, no watermarks, no phone UI.
The giant notebook page (beige graph paper, spiral optional) has a bold handwritten Chinese title EXACTLY:
「{title}」
plus a small yellow highlighter underline and a tiny question-mark doodle. No other text.
"""


def _to_canvas(src: Path, dest: Path) -> Path:
    img = Image.open(src).convert("RGB")
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
        "Photoreal vertical 9:16. Keep EVERY on-screen Chinese character, number, yellow badge, "
        "graph-paper background and notebook handwriting FROZEN — do not add, remove, or morph text. "
        "The man is a news-style presenter speaking the driving audio: natural lip sync, blinks, "
        "small head nods, finger stays pointing at the notebook title. "
        "No camera cut, no extra people, no burned-in captions, no subtitles, no watermark."
    )
    inp: dict = {
        "prompt": prompt,
        "negative_prompt": (
            "changing text, extra letters, garbled Chinese, watermark, "
            "burned-in subtitle, caption, extra fingers, identity change, mismatched lip sync"
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
    """万相常在底部胡写字幕；用定妆照底栏盖住，保留人脸嘴型。"""
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
) -> Path:
    """生成 B 方案片头 mp4（1080x1920，含口播）。口型由万相按 TTS 音频驱动。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    set_theme(category)
    title = (title or "").strip()
    line = f"今天我们要讨论的话题是：{title}"
    audio = work_dir / "host_intro.mp3"
    print(f"  [host-intro] TTS：{line}", flush=True)
    tts_synthesize(line, out_path=audio)
    audio = _pad_audio_min_duration(audio)
    # 万相 duration 只能整数秒；尽量贴近口播，少留空嘴
    dur = max(3, min(15, math.ceil(ffprobe_duration(audio) - 0.05)))

    still = work_dir / "host_intro_1080.png"
    raw = work_dir / "host_intro_raw.png"
    if reuse_still and still.is_file():
        print(f"  [host-intro] 复用定妆 {still.name}", flush=True)
    else:
        print("  [host-intro] gpt-image-2 生图 …", flush=True)
        result = generate_image(_b_prompt(title), quality="high")
        if result.get("b64_json"):
            save_b64_image(result["b64_json"], raw)
        elif result.get("url"):
            urllib.request.urlretrieve(result["url"], raw)
        else:
            raise RuntimeError(result)
        _to_canvas(raw, still)

    driven = work_dir / "host_intro_driven.mp4"
    _wan_i2v(still, driven, duration_s=dur, audio_path=audio)
    out = work_dir / "host_intro.mp4"
    scaled = work_dir / "host_intro_scaled.mp4"
    # 万相按 audio_url 出片时已自带驱动音频，直接用可避免再叠一层把嘴型错开
    if _has_audio_stream(driven):
        _cover_hallucinated_captions(driven, still, scaled)
        shutil.move(str(scaled), str(out))
    else:
        _mux_tts(driven, audio, scaled)
        _cover_hallucinated_captions(scaled, still, out)
    print(f"  [host-intro] 完成 {out} {ffprobe_duration(out):.2f}s", flush=True)
    return out
