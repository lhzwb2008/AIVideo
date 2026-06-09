"""US Market 英文口播音色配置（内置音色，非克隆）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from paths import ROOT


def _catalog_path() -> Path:
    custom = os.environ.get("US_VOICES_JSON", "").strip()
    if custom:
        return Path(custom).expanduser()
    return ROOT / "assets" / "us-voices.json"


def load_catalog() -> dict:
    path = _catalog_path()
    if not path.is_file():
        raise RuntimeError(f"未找到音色表: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_voices() -> list[dict]:
    data = load_catalog()
    voices = data.get("voices") or {}
    out: list[dict] = []
    for key, cfg in voices.items():
        if not isinstance(cfg, dict):
            continue
        out.append({"id": key, **cfg})
    return out


def resolve_voice(voice_id: str | None = None) -> tuple[str, dict]:
    data = load_catalog()
    vid = (voice_id or os.environ.get("US_TTS_VOICE") or data.get("default") or "").strip()
    voices = data.get("voices") or {}
    if vid not in voices:
        choices = ", ".join(sorted(voices))
        raise RuntimeError(f"未知 US_TTS_VOICE={vid!r}，可选: {choices}")
    return vid, voices[vid]


def apply_voice_env(voice_id: str | None = None) -> str:
    """把选中的内置英文男声写入环境变量，供 tts_client / video_compose 使用。"""
    vid, cfg = resolve_voice(voice_id)
    provider = str(cfg.get("provider") or "doubao").lower()
    os.environ["AIVIDEO_LOCALE"] = "en"
    os.environ["DASHSCOPE_TTS_PREPROCESS"] = "0"
    os.environ["DASHSCOPE_TTS_SSML"] = "0"
    os.environ["DASHSCOPE_TTS_SEGMENT"] = "0"

    if provider == "doubao":
        os.environ["TTS_PROVIDER"] = "doubao"
        os.environ["VOLCENGINE_TTS_RESOURCE_ID"] = str(cfg.get("resource_id") or "seed-tts-2.0")
        os.environ["VOLCENGINE_TTS_SPEAKER"] = str(cfg.get("speaker") or "")
        os.environ.pop("VOLCENGINE_TTS_MODEL", None)
        if cfg.get("atempo") is not None:
            os.environ["VOLCENGINE_TTS_ATEMPO"] = str(cfg["atempo"])
    else:
        os.environ["TTS_PROVIDER"] = "dashscope"
        os.environ["DASHSCOPE_TTS_MODEL"] = str(cfg.get("model") or "cosyvoice-v2")
        os.environ["DASHSCOPE_TTS_VOICE"] = str(cfg.get("voice") or "longshu_v2")
        if cfg.get("rate") is not None:
            os.environ["DASHSCOPE_TTS_RATE"] = str(cfg["rate"])
        if cfg.get("atempo") is not None:
            os.environ["DASHSCOPE_TTS_ATEMPO"] = str(cfg["atempo"])
    return vid
