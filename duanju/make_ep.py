"""漫剧合成编排：Seedance 出动态镜头 + 豆包 TTS 解说 + ffmpeg 合成竖屏成片。

用法：
  python3 sanguo/make_ep.py sanguo/ep01.json
  python3 sanguo/make_ep.py sanguo/ep01.json --shot s1   # 只生成单个镜头验证画风
  python3 sanguo/make_ep.py sanguo/ep01.json --skip-gen   # 复用已下载的镜头视频，只重合成
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research import load_env  # noqa: E402
import tts_client  # noqa: E402
import image_client  # noqa: E402
import seedance_client  # noqa: E402

VOICES = json.loads((Path(__file__).resolve().parent / "voices.json").read_text(encoding="utf-8"))


def voice_for(speaker: str) -> dict:
    return VOICES.get(speaker) or VOICES["narrator"]

W, H, FPS = 1080, 1920, 30
FONT = str(ROOT / "assets" / "HiraginoSansGB.ttc")
BGM = ROOT / "assets" / "bgm" / "03.mp3"
GAP_S = 0.28  # 句间停顿
TAIL_S = 0.4  # 每镜结尾留白


def ffprobe_dur(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    return float(out.stdout.strip())


def wrap_cn(text: str, width: int = 13) -> str:
    """按字数硬换行，照顾竖屏字幕宽度。"""
    out, line = [], ""
    for ch in text:
        line += ch
        if len(line) >= width and ch in "，。！？、 ":
            out.append(line.strip())
            line = ""
    if line.strip():
        out.append(line.strip())
    return "\n".join(out) if out else text


def esc_dt(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:")


def gen_shot_video(shot: dict, g: dict, out: Path, style_prefix: str) -> Path:
    if out.exists():
        print(f"[skip-gen] 复用 {out.name}", file=sys.stderr)
        return out
    prompt = style_prefix + shot["prompt"]
    # 方案B：按镜头出场角色，挑对应独立定妆图作为 reference_image 锚定一致性
    content = []
    chars = g.get("characters", {})
    refs = []
    if g.get("ref_image"):
        refs.append(g["ref_image"])
    for key in shot.get("refs", []):
        c = chars.get(key)
        if c and c.get("ref"):
            refs.append(c["ref"])
    refs.extend(shot.get("ref_images", []))
    for r in refs:
        rp = r if Path(r).is_absolute() else str(ROOT / r)
        content.append(seedance_client.ref_image(rp, role="reference_image"))
    print(f"[gen] {shot['id']} ({shot['seconds']}s) refs={len(content)} …", file=sys.stderr)
    seedance_client.generate(
        prompt, out,
        model=g.get("model", "doubao-seedance-2-0-260128"),
        ratio=g.get("ratio", "9:16"),
        duration=int(shot["seconds"]),
        resolution=g.get("resolution", "720p"),
        generate_audio=g.get("generate_audio", False),
        content=content or None,
    )
    return out


def _norm_lines(lines: list) -> list[dict]:
    """统一成 [{speaker,text}]，字符串默认旁白。"""
    out = []
    for ln in lines:
        if isinstance(ln, str):
            out.append({"speaker": "narrator", "text": ln})
        else:
            out.append({"speaker": ln.get("speaker", "narrator"), "text": ln["text"]})
    return out


# 角色 -> 字幕显示名
SPEAKER_LABEL = {"guanyu": "关羽", "zhangfei": "张飞", "lvbu": "吕布"}


def ensure_characters(g: dict) -> None:
    """为每个角色生成独立定妆图（已存在则跳过）。characters[key] = {ref, prompt}。"""
    for key, c in g.get("characters", {}).items():
        ref = c.get("ref")
        if not ref:
            continue
        rp = Path(ref) if Path(ref).is_absolute() else (ROOT / ref)
        if rp.exists():
            print(f"[char] 复用定妆图 {key}: {rp.name}", file=sys.stderr)
            continue
        if not c.get("prompt"):
            print(f"[char] {key} 缺 prompt 且无定妆图", file=sys.stderr)
            continue
        print(f"[char] 生成定妆图 {key} …", file=sys.stderr)
        res = image_client.generate_image(c["prompt"], size="1024x1536", quality="high")
        if res.get("b64_json"):
            image_client.save_b64_image(res["b64_json"], rp)
        else:
            import urllib.request
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_bytes(urllib.request.urlopen(res["url"], timeout=120).read())
        print(f"  -> {rp} ({rp.stat().st_size//1024} KB)", file=sys.stderr)


def build_shot_audio(lines: list, workdir: Path, shot_id: str) -> tuple[Path, list[tuple[float, float, str]]]:
    """逐句按角色音色 TTS，拼接成镜头音轨，返回每句 (start,end,字幕文本)。"""
    norm = _norm_lines(lines)
    parts, windows, cursor = [], [], 0.0
    sr = tts_client.sample_rate()
    for i, ln in enumerate(norm):
        speaker = ln["speaker"]
        text = ln["text"]
        v = voice_for(speaker)
        seg = workdir / f"{shot_id}_l{i}.mp3"
        if not seg.exists():
            tts_client.synthesize_doubao_voice(
                text, out_path=seg,
                speaker=v["speaker"], resource_id=v.get("resource_id", "seed-tts-2.0"),
                speech_rate=v.get("speech_rate", 0), tempo=v.get("tempo", 1.0),
            )
        d = ffprobe_dur(seg)
        # 角色台词加「名字：」前缀，旁白不加
        sub = text if speaker == "narrator" else f"{SPEAKER_LABEL.get(speaker, '')}：{text}"
        windows.append((cursor, cursor + d, sub))
        cursor += d + GAP_S
        parts.append(seg)

    # 拼接音频（句间插静音）
    silence = workdir / f"{shot_id}_sil.mp3"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r={sr}:cl=mono",
         "-t", f"{GAP_S:.3f}", "-c:a", "libmp3lame", "-b:a", "64k", str(silence)],
        check=True, capture_output=True,
    )
    listfile = workdir / f"{shot_id}_audio.txt"
    rows = []
    for i, p in enumerate(parts):
        rows.append(f"file '{p.resolve()}'")
        if i < len(parts) - 1:
            rows.append(f"file '{silence.resolve()}'")
    listfile.write_text("\n".join(rows) + "\n", encoding="utf-8")
    audio = workdir / f"{shot_id}_audio.mp3"
    # 勿用 -c copy：MP3 帧边界会在拼接处累积延迟，导致多句镜头字幕早于/短于配音
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
         "-c:a", "libmp3lame", "-b:a", "128k", "-ar", str(sr), "-ac", "1",
         str(audio)],
        check=True, capture_output=True,
    )
    return audio, windows


def compose_shot(video: Path, audio: Path, windows, workdir: Path, shot_id: str) -> Path:
    audio_dur = ffprobe_dur(audio)
    video_dur = ffprobe_dur(video)
    target = max(video_dur, audio_dur + TAIL_S)

    # 竖屏裁切 + 视频不足处冻结末帧补时长
    vchain = [
        f"scale={W}:{H}:force_original_aspect_ratio=increase",
        f"crop={W}:{H}", "setsar=1", f"fps={FPS}",
        f"tpad=stop_mode=clone:stop_duration={max(0.0, target - video_dur):.3f}",
    ]
    # 字幕 drawtext（逐句按时间窗显示）
    for i, (start, end, text) in enumerate(windows):
        tf = workdir / f"{shot_id}_sub{i}.txt"
        tf.write_text(wrap_cn(text), encoding="utf-8")
        vchain.append(
            f"drawtext=fontfile='{esc_dt(Path(FONT))}':textfile='{esc_dt(tf)}'"
            f":fontcolor=white:fontsize=52:line_spacing=12:text_align=center"
            f":box=1:boxcolor=black@0.55:boxborderw=22"
            f":x=(w-text_w)/2:y=h-text_h-220"
            f":enable='between(t,{start:.3f},{end:.3f})'"
        )
    vf = ",".join(vchain)

    out = workdir / f"{shot_id}_clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-i", str(audio),
         "-vf", vf, "-t", f"{target:.3f}",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-c:a", "aac", "-ar", "44100", "-b:a", "160k",
         str(out)],
        check=True, capture_output=True,
    )
    return out


def concat_clips(clips: list[Path], out: Path, workdir: Path) -> Path:
    listfile = workdir / "concat.txt"
    listfile.write_text("\n".join(f"file '{c.resolve()}'" for c in clips) + "\n", encoding="utf-8")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
         "-c", "copy", str(out)],
        check=True, capture_output=True,
    )
    return out


def add_bgm(video: Path, out: Path) -> Path:
    if not BGM.exists():
        video.replace(out)
        return out
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video), "-stream_loop", "-1", "-i", str(BGM),
         "-filter_complex",
         "[1:a]volume=0.30,afade=t=in:st=0:d=1.2[b];"
         "[0:a][b]amix=inputs=2:duration=first:dropout_transition=0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy",
         "-c:a", "aac", "-ar", "44100", "-b:a", "160k", "-shortest",
         str(out)],
        check=True, capture_output=True,
    )
    return out


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("storyboard")
    ap.add_argument("--shot", default=None, help="只处理某个镜头 id（验证画风用）")
    ap.add_argument("--skip-gen", action="store_true", help="复用已下载镜头视频，只重合成")
    args = ap.parse_args()

    sb = json.loads(Path(args.storyboard).read_text(encoding="utf-8"))
    epdir = ROOT / "sanguo" / "output" / f"ep{sb['episode']:02d}"
    shotsdir = epdir / "shots"
    workdir = epdir / "work"
    for d in (shotsdir, workdir):
        d.mkdir(parents=True, exist_ok=True)

    style = sb["style_prefix"]
    g = sb["global"]
    if not args.skip_gen:
        ensure_characters(g)
    shots = [s for s in sb["shots"] if not args.shot or s["id"] == args.shot]
    if not shots:
        print(f"找不到镜头 {args.shot}", file=sys.stderr)
        return 1

    clips = []
    failed = []
    for shot in shots:
        sv = shotsdir / f"{shot['id']}.mp4"
        if not args.skip_gen and not sv.exists():
            try:
                gen_shot_video(shot, g, sv, style)
            except Exception as e:
                print(f"[FAIL] {shot['id']} 生成失败：{str(e)[:200]}", file=sys.stderr)
                failed.append(shot["id"])
                continue
        if not sv.exists():
            print(f"[skip] 镜头视频缺失：{shot['id']}", file=sys.stderr)
            failed.append(shot["id"])
            continue
        audio, windows = build_shot_audio(shot["lines"], workdir, shot["id"])
        clip = compose_shot(sv, audio, windows, workdir, shot["id"])
        clips.append(clip)
        print(f"[clip] {shot['id']} -> {clip.name}", file=sys.stderr)

    if failed:
        print(f"\n⚠️ 以下镜头失败需重试：{failed}", file=sys.stderr)
    if not clips:
        print("没有可用镜头，终止。", file=sys.stderr)
        return 1

    if args.shot:
        # 单镜头验证：直接产出该镜头成片
        out = epdir / f"{args.shot}_preview.mp4"
        add_bgm(clips[0], out)
        print(f"\n单镜头预览完成 -> {out}")
        return 0

    merged = workdir / "merged.mp4"
    concat_clips(clips, merged, workdir)
    out = epdir / f"ep{sb['episode']:02d}.mp4"
    add_bgm(merged, out)
    print(f"\n整集合成完成 -> {out} ({out.stat().st_size // 1024 // 1024} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
