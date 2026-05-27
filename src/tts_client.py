"""百炼 DashScope CosyVoice TTS 客户端（HTTP 非流式）。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in {"1", "true", "yes", "on"}


def api_key() -> str:
    key = _env("DASHSCOPE_API_KEY")
    if not key:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY")
    return key


def base_url() -> str:
    return _env("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com").rstrip("/")


def model() -> str:
    return _env("DASHSCOPE_TTS_MODEL", "cosyvoice-v2")


def voice() -> str:
    return _env("DASHSCOPE_TTS_VOICE", "longshu_v2")


def fmt() -> str:
    return _env("DASHSCOPE_TTS_FORMAT", "mp3")


def sample_rate() -> int:
    return int(_env("DASHSCOPE_TTS_SAMPLE_RATE", "24000"))


def default_rate() -> float:
    try:
        return float(_env("DASHSCOPE_TTS_RATE", "1.0"))
    except ValueError:
        return 1.0


def atempo() -> float:
    try:
        return float(_env("DASHSCOPE_TTS_ATEMPO", "1.0"))
    except ValueError:
        return 1.0


def ssml_enabled() -> bool:
    return _env_bool("DASHSCOPE_TTS_SSML", False)


def segment_enabled() -> bool:
    return _env_bool("DASHSCOPE_TTS_SEGMENT", False)


def preprocess_enabled() -> bool:
    return _env_bool("DASHSCOPE_TTS_PREPROCESS", True)


def synth_endpoint() -> str:
    return f"{base_url()}/api/v1/services/audio/tts/SpeechSynthesizer"


# 英文缩写/品牌 → 中文口播读法（按词长降序，避免子串误替换）
_ASCII_EDGE_L = r"(?<![A-Za-z0-9])"
_ASCII_EDGE_R = r"(?![A-Za-z0-9])"

def _tok(s: str) -> re.Pattern[str]:
    return re.compile(_ASCII_EDGE_L + s + _ASCII_EDGE_R, re.I)


_TTS_REPLACEMENTS: list[tuple[re.Pattern[str], str]] = [
    (_tok(r"AI\s+Agents"), "智能体"),
    (_tok(r"AI\s+Agent"), "智能体"),
    (_tok(r"AI\s+工作负载"), "人工智能工作负载"),
    (_tok(r"AI\s+芯片"), "人工智能芯片"),
    (_tok(r"ASIC"), "专用集成电路"),
    (_tok(r"GPU"), "图形处理器"),
    (_tok(r"CPU"), "中央处理器"),
    (_tok(r"NPU"), "神经网络处理器"),
    (_tok(r"TPU"), "张量处理器"),
    (_tok(r"LLM"), "大语言模型"),
    (_tok(r"ChatGPT"), "Chat G P T"),
    (_tok(r"OpenAI"), "Open A I"),
    (_tok(r"GPT"), "G P T"),
    (_tok(r"Nvidia"), "英伟达"),
    (_tok(r"AMD"), "A M D"),
    (_tok(r"Meta"), "Meta"),
    (_tok(r"Google"), "谷歌"),
    (_tok(r"Microsoft"), "微软"),
    (_tok(r"Qualcomm"), "高通"),
    (_tok(r"ByteDance"), "字节跳动"),
    (_tok(r"API"), "A P I"),
    (_tok(r"SaaS"), "SaaS"),
    (_tok(r"AI"), "A I"),
]

_SENTENCE_SPLIT = re.compile(r"(?<=[，。！？；])")


def preprocess_tts_text(text: str) -> str:
    """口播前文本规范化：英文缩写转中文读法、清理多余空白。"""
    t = (text or "").strip()
    if not t:
        return t
    for pat, repl in _TTS_REPLACEMENTS:
        t = pat.sub(repl, t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def split_sentences(text: str) -> list[str]:
    """按中文标点切句，保留标点。"""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text) if p.strip()]
    return parts or [text]


def _break_ms_for_char(ch: str) -> int:
    if ch in "。！？":
        return 450
    if ch in "，；":
        return 280
    return 200


def text_to_ssml(text: str) -> str:
    """在标点处插入 break，改善断句与停顿。"""
    t = preprocess_tts_text(text) if preprocess_enabled() else (text or "").strip()
    if not t:
        return "<speak></speak>"
    chunks: list[str] = []
    buf: list[str] = []
    for ch in t:
        buf.append(ch)
        if ch in "，。！？；":
            chunk = "".join(buf).strip()
            if chunk:
                ms = _break_ms_for_char(ch)
                chunks.append(f"{chunk}<break time=\"{ms}ms\"/>")
            buf = []
    tail = "".join(buf).strip()
    if tail:
        chunks.append(tail)
    inner = "".join(chunks)
    return f"<speak>{inner}</speak>"


def _http_post(url: str, body: dict[str, Any], *, timeout: float = 120) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {raw[:500]}") from exc


def _download_audio(url: str, out_path: Path, *, timeout: float = 120) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        out_path.write_bytes(resp.read())
    return out_path


def _postprocess_audio(in_path: Path, out_path: Path, *, tempo: float) -> Path:
    """用 ffmpeg 做最终口播后处理：保持 TTS 自然读法，再统一提速/响度。"""
    if abs(tempo - 1.0) < 0.001:
        if in_path.resolve() != out_path.resolve():
            out_path.write_bytes(in_path.read_bytes())
        return out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)
    filters = f"atempo={tempo},loudnorm=I=-16:TP=-1.5:LRA=11"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(in_path),
            "-filter:a", filters,
            "-c:a", "libmp3lame",
            "-b:a", "128k",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path


def _synth_once(
    text: str,
    *,
    out_path: Path,
    voice_id: str | None = None,
    model_id: str | None = None,
    audio_format: str | None = None,
    sr: int | None = None,
    rate: float | None = None,
    use_ssml: bool | None = None,
    timeout: float = 120,
) -> Path:
    effective_rate = rate if rate is not None else default_rate()
    ssml = use_ssml if use_ssml is not None else ssml_enabled()
    payload_text = text_to_ssml(text) if ssml else (preprocess_tts_text(text) if preprocess_enabled() else text)

    body: dict[str, Any] = {
        "model": model_id or model(),
        "input": {
            "text": payload_text,
            "voice": voice_id or voice(),
            "format": audio_format or fmt(),
            "sample_rate": sr or sample_rate(),
            "rate": float(effective_rate),
        },
    }
    if ssml:
        body["parameters"] = {"enable_ssml": True}

    data = _http_post(synth_endpoint(), body, timeout=timeout)
    audio = ((data.get("output") or {}).get("audio") or {})
    url = audio.get("url")
    if not url:
        raise RuntimeError(f"TTS 响应缺少 audio.url: {json.dumps(data, ensure_ascii=False)[:400]}")
    return _download_audio(url, out_path, timeout=timeout)


def _ffmpeg_concat(paths: list[Path], out_path: Path, *, pause_ms: int = 250) -> Path:
    """多段 mp3 拼接，句间插入短静音。"""
    if not paths:
        raise ValueError("concat 需要至少一段音频")
    if len(paths) == 1:
        if paths[0].resolve() != out_path.resolve():
            out_path.write_bytes(paths[0].read_bytes())
        return out_path

    sr = sample_rate()
    with tempfile.TemporaryDirectory(prefix="tts_seg_") as tmp:
        tmpdir = Path(tmp)
        silence = tmpdir / "silence.mp3"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"anullsrc=r={sr}:cl=mono",
                "-t", f"{pause_ms / 1000:.3f}",
                "-c:a", "libmp3lame", "-b:a", "64k",
                str(silence),
            ],
            check=True,
            capture_output=True,
        )
        list_file = tmpdir / "concat.txt"
        lines: list[str] = []
        for i, p in enumerate(paths):
            lines.append(f"file '{p.resolve()}'")
            if i < len(paths) - 1:
                lines.append(f"file '{silence.resolve()}'")
        list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)],
            check=True,
            capture_output=True,
        )
    return out_path


def synthesize(
    text: str,
    *,
    out_path: Path,
    voice_id: str | None = None,
    model_id: str | None = None,
    audio_format: str | None = None,
    sr: int | None = None,
    rate: float | None = None,
    use_ssml: bool | None = None,
    segment: bool | None = None,
    timeout: float = 120,
) -> Path:
    """合成一段音频并下载到 out_path。默认整段合成，保留模型自然气口。"""
    t = (text or "").strip()
    if not t:
        raise ValueError("TTS 文本为空")

    tempo = atempo()
    synth_out = out_path
    tmp_ctx: tempfile.TemporaryDirectory[str] | None = None
    if abs(tempo - 1.0) >= 0.001:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="tts_post_")
        synth_out = Path(tmp_ctx.name) / out_path.name

    do_segment = segment if segment is not None else segment_enabled()
    sentences = split_sentences(preprocess_tts_text(t) if preprocess_enabled() else t)

    try:
        if do_segment and len(sentences) > 1:
            with tempfile.TemporaryDirectory(prefix="tts_parts_") as tmp:
                tmpdir = Path(tmp)
                parts: list[Path] = []
                for i, sent in enumerate(sentences):
                    part = tmpdir / f"part_{i:02d}.mp3"
                    _synth_once(
                        sent,
                        out_path=part,
                        voice_id=voice_id,
                        model_id=model_id,
                        audio_format=audio_format,
                        sr=sr,
                        rate=rate,
                        use_ssml=use_ssml,
                        timeout=timeout,
                    )
                    parts.append(part)
                _ffmpeg_concat(parts, synth_out)
        else:
            _synth_once(
                t,
                out_path=synth_out,
                voice_id=voice_id,
                model_id=model_id,
                audio_format=audio_format,
                sr=sr,
                rate=rate,
                use_ssml=use_ssml,
                timeout=timeout,
            )
        return _postprocess_audio(synth_out, out_path, tempo=tempo)
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


def main() -> int:
    import argparse
    from research import load_env
    load_env()

    parser = argparse.ArgumentParser(description="百炼 CosyVoice TTS")
    parser.add_argument("text", help="待合成文本")
    parser.add_argument("-o", "--out", default="logs/tts_out.mp3")
    parser.add_argument("--voice", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--no-ssml", action="store_true")
    parser.add_argument("--no-segment", action="store_true")
    parser.add_argument("--no-preprocess", action="store_true")
    args = parser.parse_args()

    p = synthesize(
        args.text,
        out_path=Path(args.out),
        voice_id=args.voice,
        model_id=args.model,
        use_ssml=False if args.no_ssml else None,
        segment=False if args.no_segment else None,
    )
    print(f"saved {p} ({p.stat().st_size//1024} KB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
