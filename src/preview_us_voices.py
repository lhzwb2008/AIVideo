#!/usr/bin/env python3
"""生成英文男声试听样本，供选择 US_TTS_VOICE。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from paths import ROOT
from research import load_env
from tts_client import _download_audio, _http_post, synthesize_doubao_voice, synth_endpoint
from us_voice import list_voices, resolve_voice


SAMPLE = (
    "The S and P 500 just hit a new record high. "
    "But here's the real story — chip stocks moved more after hours than during the session."
)


def _synth_dashscope(voice_cfg: dict, out: Path) -> None:
    body = {
        "model": voice_cfg.get("model") or "cosyvoice-v2",
        "input": {
            "text": SAMPLE,
            "voice": voice_cfg.get("voice") or "longshu_v2",
            "format": "mp3",
            "sample_rate": 24000,
            "rate": float(voice_cfg.get("rate") or 1.05),
        },
    }
    data = _http_post(synth_endpoint(), body, timeout=90)
    url = ((data.get("output") or {}).get("audio") or {}).get("url")
    if not url:
        raise RuntimeError(data)
    _download_audio(url, out, timeout=90)


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="生成 US Market 英文男声试听")
    parser.add_argument("--out-dir", default=str(ROOT / "output" / "voice_previews" / "doubao"))
    parser.add_argument("--voice", action="append", help="只生成指定音色 ID，可重复")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = args.voice or [row["id"] for row in list_voices()]
    meta: list[dict] = []

    for vid in ids:
        _, cfg = resolve_voice(vid)
        fname = f"{vid}.mp3"
        out = out_dir / fname
        print(f"生成 {fname} — {cfg.get('name', vid)} …", flush=True)
        try:
            provider = str(cfg.get("provider") or "dashscope").lower()
            if provider == "doubao":
                synthesize_doubao_voice(
                    SAMPLE,
                    out_path=out,
                    speaker=str(cfg.get("speaker") or ""),
                    resource_id=str(cfg.get("resource_id") or "seed-tts-2.0"),
                    tempo=float(cfg.get("atempo") or 1.08),
                )
            else:
                _synth_dashscope(cfg, out)
            meta.append({"id": vid, "file": fname, "name": cfg.get("name"), "desc": cfg.get("desc"), "ok": True})
            print(f"  ✓ {out}", flush=True)
        except Exception as exc:  # noqa: BLE001
            meta.append({"id": vid, "file": fname, "ok": False, "error": str(exc)})
            print(f"  ✗ {exc}", flush=True)

    readme = out_dir / "README.json"
    readme.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n试听目录: {out_dir}", flush=True)
    print("选定后: US_TTS_VOICE=<id> ./make-us-publish.sh", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
