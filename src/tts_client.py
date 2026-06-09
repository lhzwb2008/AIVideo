"""TTS 客户端：支持豆包声音复刻与百炼 CosyVoice。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from base64 import b64decode
from html import escape
from pathlib import Path
from typing import Any


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "1" if default else "0").lower()
    return raw in {"1", "true", "yes", "on"}


def provider() -> str:
    return _env("TTS_PROVIDER", "doubao").lower()


def api_key() -> str:
    key = _env("DASHSCOPE_API_KEY")
    if not key:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY")
    return key


def doubao_api_key() -> str:
    key = _env("VOLCENGINE_TTS_API_KEY")
    if not key:
        raise RuntimeError("缺少 VOLCENGINE_TTS_API_KEY")
    return key


def base_url() -> str:
    return _env("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com").rstrip("/")


def model() -> str:
    return _env("DASHSCOPE_TTS_MODEL", "cosyvoice-v2")


def voice() -> str:
    return _env("DASHSCOPE_TTS_VOICE", "longshu_v2")


def fmt() -> str:
    if provider() in {"doubao", "volcengine", "volc"}:
        return _env("VOLCENGINE_TTS_FORMAT", "mp3")
    return _env("DASHSCOPE_TTS_FORMAT", "mp3")


def sample_rate() -> int:
    if provider() in {"doubao", "volcengine", "volc"}:
        return int(_env("VOLCENGINE_TTS_SAMPLE_RATE", "24000"))
    return int(_env("DASHSCOPE_TTS_SAMPLE_RATE", "24000"))


def default_rate() -> float:
    try:
        return float(_env("DASHSCOPE_TTS_RATE", "1.0"))
    except ValueError:
        return 1.0


def atempo() -> float:
    try:
        if provider() in {"doubao", "volcengine", "volc"}:
            return float(_env("VOLCENGINE_TTS_ATEMPO", _env("DASHSCOPE_TTS_ATEMPO", "1.0")))
        return float(_env("DASHSCOPE_TTS_ATEMPO", "1.0"))
    except ValueError:
        return 1.0


def ssml_enabled() -> bool:
    # 默认整段合成并在标点处用 SSML break 控制停顿。这样能修正断句，
    # 又不会像分段合成那样在每个小句开头反复产生明显呼吸声。
    return _env_bool("DASHSCOPE_TTS_SSML", True)


def segment_enabled() -> bool:
    return _env_bool("DASHSCOPE_TTS_SEGMENT", False)


def preprocess_enabled() -> bool:
    return _env_bool("DASHSCOPE_TTS_PREPROCESS", True)


def synth_endpoint() -> str:
    return f"{base_url()}/api/v1/services/audio/tts/SpeechSynthesizer"


def doubao_endpoint() -> str:
    return _env("VOLCENGINE_TTS_ENDPOINT", "https://openspeech.bytedance.com/api/v3/tts/unidirectional")


def doubao_resource_id() -> str:
    return _env("VOLCENGINE_TTS_RESOURCE_ID", "seed-icl-2.0")


def doubao_speaker() -> str:
    return _env("VOLCENGINE_TTS_SPEAKER", "S_6uN8A8f22")


def doubao_model() -> str:
    return _env("VOLCENGINE_TTS_MODEL", "seed-tts-2.0-standard")


def doubao_uid() -> str:
    return _env("VOLCENGINE_TTS_UID", "aivideo")


def doubao_speech_rate() -> int:
    raw = _env("VOLCENGINE_TTS_SPEECH_RATE")
    if raw:
        try:
            return int(float(raw))
        except ValueError:
            return 0
    try:
        ratio = float(_env("VOLCENGINE_TTS_RATE", _env("DASHSCOPE_TTS_RATE", "1.0")))
    except ValueError:
        ratio = 1.0
    if ratio >= 1:
        return max(0, min(100, round((ratio - 1.0) * 100)))
    return max(-50, min(0, round((ratio - 1.0) * 100)))


def doubao_loudness_rate() -> int:
    try:
        return int(float(_env("VOLCENGINE_TTS_LOUDNESS_RATE", "0")))
    except ValueError:
        return 0


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
_EN_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])")


def locale() -> str:
    return _env("AIVIDEO_LOCALE", "zh").lower()


def is_english_locale() -> bool:
    return locale() in ("en", "english")


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
    """按标点切句；英文 locale 用 .!? 断句。"""
    text = (text or "").strip()
    if not text:
        return []
    if is_english_locale():
        parts = [p.strip() for p in _EN_SENTENCE_SPLIT.split(text) if p.strip()]
        return parts or [text]
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text) if p.strip()]
    return parts or [text]


def _break_ms_for_char(ch: str) -> int:
    if ch in ".!?":
        return 420
    if ch in "。！？":
        return 450
    if ch in ",;":
        return 260
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
        if ch in "，。！？；" or (is_english_locale() and ch in ".!?;,"):
            chunk = "".join(buf).strip()
            if chunk:
                ms = _break_ms_for_char(ch)
                chunks.append(f"{escape(chunk)}<break time=\"{ms}ms\"/>")
            buf = []
    tail = "".join(buf).strip()
    if tail:
        chunks.append(escape(tail))
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


def _doubao_post(body: dict[str, Any], *, timeout: float = 120, resource_id: str | None = None) -> list[dict[str, Any]]:
    req = urllib.request.Request(
        doubao_endpoint(),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": doubao_api_key(),
            "X-Api-Resource-Id": resource_id or doubao_resource_id(),
            "X-Api-Request-Id": str(uuid.uuid4()),
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        logid = exc.headers.get("X-Tt-Logid", "")
        suffix = f" logid={logid}" if logid else ""
        raise RuntimeError(f"豆包 TTS HTTP {exc.code}{suffix}: {raw[:500]}") from exc
    chunks: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            chunks.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"豆包 TTS 返回无法解析: {line[:300]}") from exc
    if not chunks:
        raise RuntimeError("豆包 TTS 返回为空")
    return chunks


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


def _doubao_synth_once(
    text: str,
    *,
    out_path: Path,
    voice_id: str | None = None,
    audio_format: str | None = None,
    sr: int | None = None,
    rate: float | None = None,
    timeout: float = 120,
) -> Path:
    payload_text = preprocess_tts_text(text) if preprocess_enabled() else text
    speech_rate = doubao_speech_rate()
    if rate is not None:
        speech_rate = max(-50, min(100, round((float(rate) - 1.0) * 100)))
    body: dict[str, Any] = {
        "user": {"uid": doubao_uid()},
        "namespace": "BidirectionalTTS",
        "req_params": {
            "text": payload_text,
            "speaker": voice_id or doubao_speaker(),
            "model": doubao_model(),
            "audio_params": {
                "format": audio_format or fmt(),
                "sample_rate": sr or sample_rate(),
                "speech_rate": speech_rate,
                "loudness_rate": doubao_loudness_rate(),
            },
        },
    }

    chunks = _doubao_post(body, timeout=timeout)
    audio = bytearray()
    for chunk in chunks:
        code = chunk.get("code")
        if code not in {0, 20000000, None}:
            raise RuntimeError(f"豆包 TTS 失败: {json.dumps(chunk, ensure_ascii=False)[:400]}")
        audio_b64 = chunk.get("data")
        if audio_b64:
            audio.extend(b64decode(audio_b64))
    if not audio:
        raise RuntimeError(f"豆包 TTS 响应缺少 data: {json.dumps(chunks[-1], ensure_ascii=False)[:400]}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(audio))
    return out_path


def synthesize_doubao_voice(
    text: str,
    *,
    out_path: Path,
    speaker: str,
    resource_id: str = "seed-tts-2.0",
    req_model: str | None = None,
    audio_format: str = "mp3",
    sr: int = 24000,
    speech_rate: int = 0,
    loudness_rate: int = 0,
    tempo: float = 1.0,
    timeout: float = 120,
) -> Path:
    """用指定豆包音色合成（支持官方 2.0 音色 / 克隆音色）。

    - 官方 2.0 音色（*_uranus_bigtts）：resource_id="seed-tts-2.0"，req_model 留空。
    - 克隆音色（S_xxx）：resource_id="seed-icl-2.0"，req_model="seed-tts-2.0-standard"。
    """
    payload_text = preprocess_tts_text(text) if preprocess_enabled() else text
    req_params: dict[str, Any] = {
        "text": payload_text,
        "speaker": speaker,
        "audio_params": {
            "format": audio_format,
            "sample_rate": sr,
            "speech_rate": speech_rate,
            "loudness_rate": loudness_rate,
        },
    }
    if req_model:
        req_params["model"] = req_model
    body = {"user": {"uid": doubao_uid()}, "namespace": "BidirectionalTTS", "req_params": req_params}

    synth_out = out_path
    tmp_ctx: tempfile.TemporaryDirectory[str] | None = None
    if abs(tempo - 1.0) >= 0.001:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="tts_post_")
        synth_out = Path(tmp_ctx.name) / out_path.name
    try:
        chunks = _doubao_post(body, timeout=timeout, resource_id=resource_id)
        audio = bytearray()
        for chunk in chunks:
            code = chunk.get("code")
            if code not in {0, 20000000, None}:
                raise RuntimeError(f"豆包 TTS 失败: {json.dumps(chunk, ensure_ascii=False)[:300]}")
            if chunk.get("data"):
                audio.extend(b64decode(chunk["data"]))
        if not audio:
            raise RuntimeError(f"豆包 TTS 无音频: speaker={speaker}")
        synth_out.parent.mkdir(parents=True, exist_ok=True)
        synth_out.write_bytes(bytes(audio))
        return _postprocess_audio(synth_out, out_path, tempo=tempo)
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


def _ffmpeg_concat(paths: list[Path], out_path: Path, *, pause_ms: int = 250, sr: int | None = None) -> Path:
    """多段 mp3 拼接，句间插入短静音。"""
    if not paths:
        raise ValueError("concat 需要至少一段音频")
    if len(paths) == 1:
        if paths[0].resolve() != out_path.resolve():
            out_path.write_bytes(paths[0].read_bytes())
        return out_path

    audio_sr = sr or sample_rate()
    with tempfile.TemporaryDirectory(prefix="tts_seg_") as tmp:
        tmpdir = Path(tmp)
        silence = tmpdir / "silence.mp3"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"anullsrc=r={audio_sr}:cl=mono",
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


def _synthesize_doubao(
    text: str,
    *,
    out_path: Path,
    voice_id: str | None = None,
    audio_format: str | None = None,
    sr: int | None = None,
    rate: float | None = None,
    segment: bool | None = None,
    timeout: float = 120,
) -> Path:
    tempo = atempo()
    synth_out = out_path
    tmp_ctx: tempfile.TemporaryDirectory[str] | None = None
    if abs(tempo - 1.0) >= 0.001:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="tts_post_")
        synth_out = Path(tmp_ctx.name) / out_path.name

    do_segment = segment if segment is not None else segment_enabled()
    sentences = split_sentences(preprocess_tts_text(text) if preprocess_enabled() else text)

    try:
        if do_segment and len(sentences) > 1:
            with tempfile.TemporaryDirectory(prefix="tts_parts_") as tmp:
                tmpdir = Path(tmp)
                parts: list[Path] = []
                for i, sent in enumerate(sentences):
                    part = tmpdir / f"part_{i:02d}.mp3"
                    _doubao_synth_once(
                        sent,
                        out_path=part,
                        voice_id=voice_id,
                        audio_format=audio_format,
                        sr=sr,
                        rate=rate,
                        timeout=timeout,
                    )
                    parts.append(part)
                _ffmpeg_concat(parts, synth_out, sr=sr)
        else:
            _doubao_synth_once(
                text,
                out_path=synth_out,
                voice_id=voice_id,
                audio_format=audio_format,
                sr=sr,
                rate=rate,
                timeout=timeout,
            )
        return _postprocess_audio(synth_out, out_path, tempo=tempo)
    finally:
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


def _synthesize_dashscope(
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
    tempo = atempo()
    synth_out = out_path
    tmp_ctx: tempfile.TemporaryDirectory[str] | None = None
    if abs(tempo - 1.0) >= 0.001:
        tmp_ctx = tempfile.TemporaryDirectory(prefix="tts_post_")
        synth_out = Path(tmp_ctx.name) / out_path.name

    do_segment = segment if segment is not None else segment_enabled()
    sentences = split_sentences(preprocess_tts_text(text) if preprocess_enabled() else text)

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
                text,
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

    if provider() in {"dashscope", "bailian", "aliyun"}:
        return _synthesize_dashscope(
            t,
            out_path=out_path,
            voice_id=voice_id,
            model_id=model_id,
            audio_format=audio_format,
            sr=sr,
            rate=rate,
            use_ssml=use_ssml,
            segment=segment,
            timeout=timeout,
        )
    if provider() in {"doubao", "volcengine", "volc"}:
        return _synthesize_doubao(
            t,
            out_path=out_path,
            voice_id=voice_id,
            audio_format=audio_format,
            sr=sr,
            rate=rate,
            segment=segment,
            timeout=timeout,
        )
    raise RuntimeError(f"未知 TTS_PROVIDER={provider()}，可选 doubao 或 dashscope")


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
