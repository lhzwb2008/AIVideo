"""百炼 DashScope CosyVoice TTS 客户端（HTTP 非流式）。"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


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
        return float(_env("DASHSCOPE_TTS_RATE", "1.2"))
    except ValueError:
        return 1.2


def synth_endpoint() -> str:
    return f"{base_url()}/api/v1/services/audio/tts/SpeechSynthesizer"


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


def synthesize(
    text: str,
    *,
    out_path: Path,
    voice_id: str | None = None,
    model_id: str | None = None,
    audio_format: str | None = None,
    sr: int | None = None,
    rate: float | None = None,
    timeout: float = 120,
) -> Path:
    """合成一段音频并下载到 out_path。返回 out_path。"""
    effective_rate = rate if rate is not None else default_rate()
    body: dict[str, Any] = {
        "model": model_id or model(),
        "input": {
            "text": text,
            "voice": voice_id or voice(),
            "format": audio_format or fmt(),
            "sample_rate": sr or sample_rate(),
            "rate": float(effective_rate),
        },
    }

    data = _http_post(synth_endpoint(), body, timeout=timeout)
    audio = ((data.get("output") or {}).get("audio") or {})
    url = audio.get("url")
    if not url:
        raise RuntimeError(f"TTS 响应缺少 audio.url: {json.dumps(data, ensure_ascii=False)[:400]}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        out_path.write_bytes(resp.read())
    return out_path


def main() -> int:
    import argparse
    from research import load_env
    load_env()

    parser = argparse.ArgumentParser(description="百炼 CosyVoice TTS")
    parser.add_argument("text", help="待合成文本")
    parser.add_argument("-o", "--out", default="logs/tts_out.mp3")
    parser.add_argument("--voice", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    p = synthesize(args.text, out_path=Path(args.out), voice_id=args.voice, model_id=args.model)
    print(f"saved {p} ({p.stat().st_size//1024} KB)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
