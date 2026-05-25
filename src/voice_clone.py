#!/usr/bin/env python3
"""百炼声音克隆：本地音频/视频 → 临时 OSS → create_voice → voice_id。

用法:
    python3 src/voice_clone.py path/to/audio_or_video.mp4 [--prefix boss]

视频自动 ffmpeg 抽 30s 单声道 mp3，再上传克隆。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from paths import ROOT
from research import load_env


CUSTOMIZATION_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
UPLOADS_URL = "https://dashscope.aliyuncs.com/api/v1/uploads"


def _api_key() -> str:
    import os
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY")
    return key


def extract_audio(src: Path, *, duration: int = 30, start: int = 5) -> Path:
    """从视频/音频抽取 30s 单声道 mp3 用于克隆。"""
    if src.suffix.lower() in {".mp3", ".wav", ".m4a"} and duration <= 0:
        return src
    out = ROOT / "logs" / f"clone_input_{src.stem}.mp3"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-ss", str(start), "-t", str(duration),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "libmp3lame", "-b:a", "96k",
        str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 抽音失败:\n{proc.stderr[-1000:]}")
    return out


def get_upload_policy(model: str) -> dict:
    req = urllib.request.Request(
        f"{UPLOADS_URL}?action=getPolicy&model={model}",
        headers={"Authorization": f"Bearer {_api_key()}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["data"]


def upload_to_dashscope_oss(file_path: Path, model: str) -> str:
    """上传到百炼临时 OSS，返回 oss:// URL（48h 有效）。"""
    policy = get_upload_policy(model)
    key = f"{policy['upload_dir']}/{file_path.name}"
    boundary = "----AIVoice" + secrets.token_hex(8)
    ctype = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"

    def field(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        ).encode()

    body = b""
    for n, v in (
        ("OSSAccessKeyId", policy["oss_access_key_id"]),
        ("Signature", policy["signature"]),
        ("policy", policy["policy"]),
        ("x-oss-object-acl", policy["x_oss_object_acl"]),
        ("x-oss-forbid-overwrite", policy["x_oss_forbid_overwrite"]),
        ("key", key),
        ("success_action_status", "200"),
    ):
        body += field(n, v)
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode() + file_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        policy["upload_host"],
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        if resp.status != 200:
            raise RuntimeError(f"上传失败 status={resp.status}")
    return f"oss://{key}"


def create_voice(
    audio_url: str,
    *,
    target_model: str = "cosyvoice-v2",
    prefix: str = "boss",
    language_hints: list[str] | None = None,
) -> str:
    body = {
        "model": "voice-enrollment",
        "input": {
            "action": "create_voice",
            "target_model": target_model,
            "prefix": prefix,
            "url": audio_url,
            "language_hints": language_hints or ["zh"],
        },
    }
    req = urllib.request.Request(
        CUSTOMIZATION_URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
            "X-DashScope-OssResourceResolve": "enable",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            d = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"create_voice 失败: HTTP {e.code} {e.read().decode()[:400]}") from e
    vid = (d.get("output") or {}).get("voice_id") or (d.get("output") or {}).get("voice")
    if not vid:
        raise RuntimeError(f"未拿到 voice_id: {json.dumps(d, ensure_ascii=False)[:400]}")
    return str(vid)


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="百炼声音克隆：从音/视频生成 voice_id")
    parser.add_argument("src", help="本地音频/视频路径（.mp3/.wav/.mp4 等）")
    parser.add_argument("--target-model", default="cosyvoice-v2")
    parser.add_argument("--prefix", default="boss")
    parser.add_argument("--clip", type=int, default=30, help="抽取时长（秒），默认 30")
    parser.add_argument("--clip-start", type=int, default=5, help="起始秒数，默认 5（避开开头）")
    args = parser.parse_args()

    src = Path(args.src)
    if not src.is_file():
        print(f"文件不存在: {src}", file=sys.stderr); return 1

    print(f"[1/3] 抽取音频 {args.clip}s …", file=sys.stderr)
    audio = extract_audio(src, duration=args.clip, start=args.clip_start)
    print(f"  → {audio} ({audio.stat().st_size//1024} KB)", file=sys.stderr)

    print(f"[2/3] 上传到百炼临时 OSS …", file=sys.stderr)
    oss_url = upload_to_dashscope_oss(audio, args.target_model)
    print(f"  → {oss_url}", file=sys.stderr)

    print(f"[3/3] 创建克隆音色（target={args.target_model}, prefix={args.prefix}） …", file=sys.stderr)
    vid = create_voice(oss_url, target_model=args.target_model, prefix=args.prefix)
    print(f"  ✓ voice_id: {vid}", file=sys.stderr)

    out = ROOT / "logs" / "cloned_voice_id.txt"
    out.write_text(vid + "\n")
    print(f"\n已保存到 {out}", file=sys.stderr)
    print("\n下一步：把这一行写入 .env：", file=sys.stderr)
    print(f"DASHSCOPE_TTS_VOICE={vid}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
