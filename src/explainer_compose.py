#!/usr/bin/env python3
"""动态 PPT 合成：替代「生图 + 漫画合成」。口播沿用脚本原文，画面由 HTML 模板逐帧渲染。

画风沿用漫画版：米黄方格纸、圆珠笔线条、黄/紫荧光笔、手写字。版式、数字和图表按口播逐个画出来。
输入输出与 video_compose.py 一致：读 last_script_*.json，成片写 output/zh/<时间戳>.mp4，
并更新 logs/zh/last_video.txt、video_manifest.jsonl、compose/<stem>/cover.png，
每页定格截图写回 slides[i].image_path 供论坛图文使用。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

import categories
import video_compose as vc
from explainer_plan import phrases_of, plan_visuals
from locale_env import locale_logs_dir, locale_output_dir
from paths import ROOT, ffmpeg_executable
from research import load_env
from tts_client import synthesize

W, H, FPS = 1080, 1920, 30
LEAD = 0.3
TAIL = 0.35
TEMPLATE = Path(__file__).with_name("explainer_template.html")
FONT = ROOT / "assets" / "fonts" / "LXGWMarkerGothic-Regular.ttf"
COVER_DOODLE = "chart"
DISCLAIMER = "内容基于公开信息整理，不构成投资建议"


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _workers() -> int:
    return max(1, int(os.environ.get("AIVIDEO_EXPLAINER_WORKERS", "4")))


def _plan(data: dict, script: dict, work: Path, *, replan: bool) -> dict:
    key = hashlib.sha1(
        json.dumps(
            [script.get("title"), script.get("cold_open"), [s.get("narration") for s in script.get("slides") or []]],
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()[:12]
    cache = work / "explainer_plan.json"
    if cache.is_file() and not replan:
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("key") == key:
            _log("[explainer] 复用已有版式规划")
            return saved["plan"]
    plan = plan_visuals(data, script)
    cache.write_text(json.dumps({"key": key, "plan": plan}, ensure_ascii=False, indent=2), encoding="utf-8")
    return plan


def _phrases(text: str) -> list[str]:
    """与版式规划同一套分句（cue 编号要对得上），只把被硬切成两半的 LPR、GPU 这类英文词挪回一句。"""
    out = phrases_of(text)
    for i in range(len(out) - 1):
        m = re.search(r"[A-Za-z0-9.%]+$", out[i])
        hard_cut = (out[i] + out[i + 1]) in text
        if hard_cut and m and re.match(r"[A-Za-z0-9]", out[i + 1]) and m.start() > 0:
            out[i + 1] = out[i][m.start():] + out[i + 1]
            out[i] = out[i][: m.start()]
    return out


def _duration(path: Path) -> float:
    text = subprocess.run(
        [ffmpeg_executable(), "-hide_banner", "-i", str(path), "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
    if not m:
        raise RuntimeError(f"读不到时长: {path}")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def _has_audio(path: Path) -> bool:
    text = subprocess.run(
        [ffmpeg_executable(), "-hide_banner", "-i", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stderr
    return "Audio:" in text


def _narrate(texts: list[str], work: Path) -> list[Path]:
    work.mkdir(parents=True, exist_ok=True)

    def one(i: int) -> Path:
        out = work / f"voice_{i:02d}_{hashlib.md5(texts[i].encode('utf-8')).hexdigest()[:8]}.mp3"
        if not (out.is_file() and out.stat().st_size > 1000):
            _log(f"  配音 {i + 1}/{len(texts)}")
            synthesize(texts[i], out_path=out)
        return out

    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(one, range(len(texts))))


def _subs(phrases: list[str], spans: list[tuple[float, float]], offset: float, floor: float = 0.0) -> list[dict]:
    out = []
    for text, (a, b) in zip(phrases, spans):
        t0, t1 = max(offset + a, floor), offset + b
        if text.strip() and t1 - t0 > 0.05:
            out.append({"lines": vc._wrap_subtitle_lines(text) or [text], "t0": round(t0, 3), "t1": round(t1, 3)})
    return out


def _badge_png(path: Path) -> Path:
    im = Image.new("RGBA", (W, 400), (0, 0, 0, 0))
    vc._draw_brand_badge(ImageDraw.Draw(im))
    im.save(path, "PNG")
    return path


def _prepare(script_file: Path, *, replan: bool = False):
    data = json.loads(script_file.read_text(encoding="utf-8"))
    script = data.get("script", data)
    slides = script.get("slides") or []
    if not slides:
        raise ValueError("脚本无 slides")
    load_env()
    vc._load_brand_outro_config()
    category = categories.resolve_category(script, os.environ.get("AIVIDEO_CATEGORY"))
    vc.set_theme(category)
    try:
        from research import print_douyin_pre_publish_scan

        print_douyin_pre_publish_scan(script)
    except Exception as exc:  # noqa: BLE001
        _log(f"[douyin预审] 跳过：{exc}")

    work = locale_logs_dir() / "compose" / script_file.stem
    ex = work / "explainer"
    ex.mkdir(parents=True, exist_ok=True)
    plan = _plan(data, script, work, replan=replan)
    hold = vc.cover_duration_s()

    title = str(script.get("title") or "").strip()
    cold = str(script.get("cold_open") or "").strip() or title
    seed = hashlib.sha1(f"{datetime.now():%Y%m%d}|{script_file.stem}".encode("utf-8")).digest()
    variants = vc.OUTRO_NARRATION_VARIANTS or [vc.OUTRO_NARRATION]
    outro_text = variants[seed[0] % len(variants)]
    texts = [cold] + [str(s.get("narration") or "") for s in slides] + [outro_text]
    _log("[explainer] 配音")
    audios = _narrate(texts, ex)

    pages: list[dict] = []
    voices: list[tuple[Path, float]] = []
    subs: list[dict] = []

    adur = _duration(audios[0])
    lo = max(1.5, vc._env_float("AIVIDEO_COLD_OPEN_MIN_S", 2.5))
    hi = max(lo, vc._env_float("AIVIDEO_COLD_OPEN_MAX_S", 4.5), vc._env_float("AIVIDEO_COLD_OPEN_AUDIO_MAX_S", 8.0))
    cover_end = max(lo, hold + 0.6, min(hi, adur + 0.25))
    ph = _phrases(cold)
    subs += _subs(ph, vc.allocate_phrase_times(ph, adur), 0.0, floor=hold)
    voices.append((audios[0], 0.0))
    pages.append({"type": "cover", **plan["cover"], "start": 0.0, "end": round(cover_end, 3)})

    t = cover_end
    for i, (slide, vis) in enumerate(zip(slides, plan["slides"]), start=1):
        dur = _duration(audios[i])
        ph = _phrases(str(slide.get("narration") or ""))
        spans = vc.allocate_phrase_times(ph, dur)
        audio_at = t + LEAD
        pages.append({
            "type": "content",
            "idx": i,
            "count": len(slides),
            "title": vis.get("title") or str(slide.get("headline") or slide.get("chapter_title") or "")[:14],
            "visual": vis,
            "cues": [round(audio_at + a, 3) for a, _ in spans],
            "start": round(t, 3),
            "end": round(audio_at + dur + TAIL, 3),
        })
        subs += _subs(ph, spans, audio_at)
        voices.append((audios[i], audio_at))
        t = pages[-1]["end"]

    odur = _duration(audios[-1])
    pages.append({"type": "end", "start": round(t, 3), "end": round(t + max(2.0, odur + 0.6), 3)})
    voices.append((audios[-1], t + 0.15))
    total = pages[-1]["end"]
    _log(f"[explainer] 总时长 {total:.1f}s，{len(pages)} 页")

    date = datetime.now().strftime("%Y.%m.%d")
    payload = {
        "slides": pages,
        "subs": subs,
        "hold": hold,
        "total": total,
        "badge": _badge_png(ex / "badge.png").as_uri(),
        "font": FONT.as_uri(),
        "kicker": " · ".join(x for x in (categories.label_of(category), date) if x),
        "brand": vc.BRAND_NAME,
        "tagline": vc.BRAND_TAGLINE,
        "outro_headline": vc.OUTRO_HEADLINE,
        "outro_subline": vc.OUTRO_SUBLINE,
        "doodle": COVER_DOODLE,
        "disclaimer": DISCLAIMER,
    }
    html = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/", json.dumps(payload, ensure_ascii=False))
    page = ex / "explainer_page.html"
    page.write_text(html, encoding="utf-8")
    return data, script, work, page, pages, voices, total, cover_end


def _stills(pages: list[dict], script_file: Path, work: Path) -> list[tuple[float, Path]]:
    """封面 + 每页定格（不带字幕），给发布封面和论坛图文用。"""
    img_dir = locale_logs_dir() / "images" / script_file.stem
    out = [(pages[0]["end"] - 0.05, work / "cover.png")]
    for p in pages[1:-1]:
        out.append((p["end"] - 0.45, img_dir / f"slide_{p['idx']:02d}.png"))
    return out


def _launch_kwargs() -> dict:
    kw: dict = {"headless": True, "args": ["--allow-file-access-from-files"]}
    exe = os.environ.get("LOCAL_CHROME_PATH", "").strip()
    if exe and Path(exe).is_file():
        kw["executable_path"] = exe
    else:
        kw["channel"] = "chrome"
    return kw


async def _open_page(browser, page_path: Path):
    page = await browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
    await page.goto(page_path.as_uri())
    await page.wait_for_function("window.__ready === true", timeout=120_000)
    return page


async def _render(page_path: Path, frames: int, frame_dir: Path, stills: list[tuple[float, Path]]) -> int:
    from playwright.async_api import async_playwright

    workers = _workers()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**_launch_kwargs())

        async def lane(k: int) -> int:
            page = await _open_page(browser, page_path)
            n = 0
            for f in range(k, frames, workers):
                await page.evaluate(f"window.seek({f / FPS})")
                await page.screenshot(path=str(frame_dir / f"f_{f:05d}.jpg"), type="jpeg", quality=92)
                n += 1
            await page.close()
            return n

        counts = await asyncio.gather(*(lane(k) for k in range(workers)))
        page = await _open_page(browser, page_path)
        await page.evaluate("window.setHideSubs(true)")
        for t, path in stills:
            path.parent.mkdir(parents=True, exist_ok=True)
            await page.evaluate(f"window.seek({t})")
            await page.screenshot(path=str(path), type="png")
        await page.close()
        await browser.close()
    return sum(counts)


def _encode(frame_dir: Path, out: Path) -> Path:
    subprocess.run(
        [
            ffmpeg_executable(), "-y", "-loglevel", "error",
            "-framerate", str(FPS), "-i", str(frame_dir / "f_%05d.jpg"),
            "-vf", "scale=in_range=pc:out_range=tv,format=yuv420p",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-color_range", "tv", str(out),
        ],
        check=True,
    )
    return out


def _mix_voice(voices: list[tuple[Path, float]], silent: Path, total: float, out: Path) -> Path:
    cmd = [ffmpeg_executable(), "-y", "-loglevel", "error", "-i", str(silent)]
    filters = []
    for i, (vo, at) in enumerate(voices):
        cmd += ["-i", str(vo)]
        ms = int(at * 1000)
        filters.append(f"[{i + 1}:a]aresample={vc.TTS_SAMPLE_RATE},adelay={ms}|{ms}[v{i}]")
    labels = "".join(f"[v{i}]" for i in range(len(voices)))
    filters.append(f"{labels}amix=inputs={len(voices)}:normalize=0:duration=longest,apad,atrim=0:{total:.3f}[a]")
    cmd += [
        "-filter_complex", ";".join(filters),
        "-map", "0:v", "-map", "[a]", "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k", "-ar", str(vc.TTS_SAMPLE_RATE), "-ac", "2",
        "-t", f"{total:.3f}", "-movflags", "+faststart", str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


def _splice_intro(main: Path, intro: Path, at: float, out: Path) -> Path:
    """数字人片头插在冷开场之后，和静态漫画版的顺序一致。"""
    sr = vc.TTS_SAMPLE_RATE
    v1 = (
        f"[1:v]scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0xFBF6E4,fps={FPS},format=yuv420p,setsar=1[v1]"
    )
    if _has_audio(intro):
        a1 = f"[1:a]aresample={sr},aformat=channel_layouts=stereo[a1]"
    else:
        a1 = f"aevalsrc=0:c=stereo:s={sr}:d={_duration(intro):.3f}[a1]"
    fc = ";".join([
        f"[0:v]trim=0:{at:.3f},setpts=PTS-STARTPTS,setsar=1[v0]",
        f"[0:a]atrim=0:{at:.3f},asetpts=PTS-STARTPTS[a0]",
        v1, a1,
        f"[0:v]trim={at:.3f},setpts=PTS-STARTPTS,setsar=1[v2]",
        f"[0:a]atrim={at:.3f},asetpts=PTS-STARTPTS[a2]",
        "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]",
    ])
    subprocess.run(
        [
            ffmpeg_executable(), "-y", "-loglevel", "error", "-i", str(main), "-i", str(intro),
            "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k", "-ar", str(sr), "-ac", "2", str(out),
        ],
        check=True,
    )
    return out


def compose(script_file: Path, *, output: Path | None = None, replan: bool = False) -> Path:
    data, script, work, page, pages, voices, total, cover_end = _prepare(script_file, replan=replan)
    ex = work / "explainer"
    frames = int(round(total * FPS))
    frame_dir = ex / "frames"
    shutil.rmtree(frame_dir, ignore_errors=True)
    frame_dir.mkdir()
    stills = _stills(pages, script_file, work)
    _log(f"[explainer] 渲染 {frames} 帧（{total:.1f}s，{_workers()} 路并行）")
    done = asyncio.run(_render(page, frames, frame_dir, stills))
    if done != frames:
        raise RuntimeError(f"帧数不对：{done}/{frames}")
    silent = _encode(frame_dir, ex / "explainer_silent.mp4")
    shutil.rmtree(frame_dir, ignore_errors=True)
    voiced = _mix_voice(voices, silent, total, ex / "explainer_voice.mp4")

    title = str(script.get("title") or "").strip()
    if getattr(vc, "host_intro_enabled", lambda: False)() and title:
        intro = vc._try_host_intro(title, work_dir=work, script=script)
        if intro:
            voiced = _splice_intro(voiced, intro, cover_end, ex / "explainer_intro.mp4")

    output = output or (locale_output_dir() / f"{datetime.now():%Y%m%d_%H%M%S}.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)
    bgm = vc.select_bgm(script_file.stem)
    if bgm:
        _log(f"[bgm] 混入背景音乐：{bgm.name}")
        vc.mix_bgm(video_path=voiced, bgm_path=bgm, out_path=output)
    else:
        shutil.copyfile(voiced, output)

    for s, (_, path) in zip(script.get("slides") or [], stills[1:]):
        s["image_path"] = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
    script["explainer"] = {"layouts": [p["visual"]["layout"] for p in pages[1:-1]], "duration": round(total, 2)}
    script_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    dur_s = _duration(output)
    max_dur = vc.max_video_duration_s()
    if max_dur > 0 and dur_s > max_dur + 0.5:
        _log(f"⚠️  成片 {dur_s:.1f}s 超过上限 {max_dur:.0f}s（AIVIDEO_MAX_VIDEO_DURATION_S）")
    _log(f"完成：{output} ({output.stat().st_size // 1024} KB, {dur_s:.1f}s)")

    (locale_logs_dir() / "last_video.txt").write_text(str(output) + "\n", encoding="utf-8")
    with (locale_logs_dir() / "video_manifest.jsonl").open("a", encoding="utf-8") as mf:
        mf.write(json.dumps({
            "video": str(output),
            "script": str(script_file),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "compose": "explainer",
            "bgm": str(bgm) if bgm else "",
        }, ensure_ascii=False) + "\n")
    return output


async def _preview(page_path: Path, shots: list[tuple[float, Path]]) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**_launch_kwargs())
        page = await _open_page(browser, page_path)
        for t, path in shots:
            await page.evaluate(f"window.seek({t})")
            await page.screenshot(path=str(path), type="jpeg", quality=85)
            print(path)
        await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="脚本 JSON → 动态 PPT 竖屏视频")
    parser.add_argument("script_file")
    parser.add_argument("-o", "--output")
    parser.add_argument("--replan", action="store_true", help="忽略缓存，重新规划版式")
    parser.add_argument("--preview", type=float, nargs="*", help="只截这些时间点的画面，不出片；不给时间就截每页定格")
    args = parser.parse_args()
    path = Path(args.script_file)
    if not path.is_file():
        _log(f"脚本不存在: {path}")
        return 1
    if args.preview is not None:
        _, _, work, page, pages, _, total, _ = _prepare(path, replan=args.replan)
        out = work / "explainer"
        times = args.preview or [p["end"] - 0.45 for p in pages]
        asyncio.run(_preview(page, [(t, out / f"preview_{t:07.2f}.jpg") for t in times]))
        return 0
    compose(path, output=Path(args.output) if args.output else None, replan=args.replan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
