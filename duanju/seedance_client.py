"""Seedance 视频生成客户端（走 AiHubMix 统一视频接口）。

复用 .env 里的 AIHUBMIX_API_KEY / AIHUBMIX_BASE_URL。
异步三步：提交任务 -> 轮询 status -> 下载 mp4。
"""

from __future__ import annotations

import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def api_key() -> str:
    key = _env("AIHUBMIX_API_KEY")
    if not key:
        raise RuntimeError("缺少 AIHUBMIX_API_KEY")
    return key


def base_url() -> str:
    raw = _env("AIHUBMIX_BASE_URL", "https://api.inferera.com/v1").rstrip("/")
    # 视频接口挂在 /v1 下，base 可能已带 /v1 也可能没带，统一归一到根域名。
    if raw.endswith("/v1"):
        return raw[: -len("/v1")]
    return raw


def _headers(json_body: bool = True) -> dict[str, str]:
    h = {"Authorization": f"Bearer {api_key()}"}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _urlopen_retry(req: urllib.request.Request, *, timeout: float, what: str, retries: int = 5):
    """带退避重试的 urlopen，吞掉瞬时网络/SSL 抖动；HTTP 4xx/5xx 仍按错误抛出文本。"""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return urllib.request.urlopen(req, timeout=timeout).read()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            # 5xx 可重试，4xx 直接报错
            if exc.code >= 500 and attempt < retries:
                last_exc = RuntimeError(f"{what} HTTP {exc.code}: {raw[:300]}")
            else:
                raise RuntimeError(f"{what} HTTP {exc.code}: {raw[:600]}") from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_exc = exc
        wait = min(30, 3 * attempt)
        print(f"    [retry {attempt}/{retries}] {what} 失败({last_exc})，{wait}s 后重试", file=sys.stderr)
        time.sleep(wait)
    raise RuntimeError(f"{what} 连续 {retries} 次失败: {last_exc}")


def _post(path: str, body: dict[str, Any], *, timeout: float = 120) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{base_url()}{path}",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=_headers(),
        method="POST",
    )
    return json.loads(_urlopen_retry(req, timeout=timeout, what="Seedance 提交").decode())


def _get(path: str, *, timeout: float = 60) -> dict[str, Any]:
    req = urllib.request.Request(f"{base_url()}{path}", headers=_headers(False), method="GET")
    return json.loads(_urlopen_retry(req, timeout=timeout, what="Seedance 查询").decode())


def file_to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(str(path))[0] or "image/png"
    b64 = base64.b64encode(Path(path).read_bytes()).decode()
    return f"data:{mime};base64,{b64}"


def ref_image(url_or_path: str, role: str = "reference_image") -> dict[str, Any]:
    """构造一个参考图 content 项。url_or_path 可为 http(s) URL 或本地文件路径。"""
    if url_or_path.startswith(("http://", "https://", "data:")):
        url = url_or_path
    else:
        url = file_to_data_url(Path(url_or_path))
    return {"type": "image_url", "image_url": {"url": url}, "role": role}


def submit(
    prompt: str,
    *,
    model: str = "doubao-seedance-2-0-260128",
    ratio: str = "9:16",
    duration: int = 8,
    resolution: str = "720p",
    generate_audio: bool = False,
    watermark: bool = False,
    content: list[dict[str, Any]] | None = None,
) -> str:
    """提交一个生成任务，返回 video_id。"""
    extra: dict[str, Any] = {
        "ratio": ratio,
        "duration": int(duration),
        "resolution": resolution,
        "generate_audio": generate_audio,
        "watermark": watermark,
    }
    if content:
        extra["content"] = content
    body = {"model": model, "prompt": prompt, "extra_body": extra}
    data = _post("/v1/videos", body)
    vid = data.get("id")
    if not vid:
        raise RuntimeError(f"提交响应缺少 id: {json.dumps(data, ensure_ascii=False)[:400]}")
    return vid


def poll(video_id: str, *, interval: float = 15, timeout_s: float = 900) -> dict[str, Any]:
    """轮询直到 completed/failed。"""
    deadline = time.time() + timeout_s
    last = {}
    while time.time() < deadline:
        last = _get(f"/v1/videos/{video_id}")
        status = last.get("status")
        prog = last.get("progress", "")
        print(f"  [{video_id[:12]}…] status={status} progress={prog}", file=sys.stderr)
        if status == "completed":
            return last
        if status == "failed":
            err = last.get("error") or {}
            msg = err.get("message") if isinstance(err, dict) else err
            raise RuntimeError(f"Seedance 生成失败: {msg}")
        time.sleep(interval)
    raise RuntimeError(f"Seedance 轮询超时（{timeout_s}s）: {json.dumps(last, ensure_ascii=False)[:300]}")


def download(video_id: str, out_path: Path, *, timeout: float = 300) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(
        f"{base_url()}/v1/videos/{video_id}/content", headers=_headers(False), method="GET"
    )
    out_path.write_bytes(_urlopen_retry(req, timeout=timeout, what="Seedance 下载"))
    return out_path


def generate(prompt: str, out_path: Path, **kwargs: Any) -> Path:
    """一站式：提交 -> 轮询 -> 下载。"""
    vid = submit(prompt, **kwargs)
    print(f"  提交成功 video_id={vid}", file=sys.stderr)
    poll(vid)
    download(vid, out_path)
    print(f"  已下载 {out_path} ({out_path.stat().st_size // 1024} KB)", file=sys.stderr)
    return out_path


def main() -> int:
    import argparse
    from research import load_env

    load_env()
    p = argparse.ArgumentParser(description="Seedance 单镜头测试")
    p.add_argument("prompt")
    p.add_argument("-o", "--out", default="sanguo/output/test.mp4")
    p.add_argument("--seconds", type=int, default=8)
    p.add_argument("--ratio", default="9:16")
    p.add_argument("--resolution", default="720p")
    p.add_argument("--model", default="doubao-seedance-2-0-260128")
    args = p.parse_args()
    generate(
        args.prompt,
        Path(args.out),
        model=args.model,
        ratio=args.ratio,
        duration=args.seconds,
        resolution=args.resolution,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
