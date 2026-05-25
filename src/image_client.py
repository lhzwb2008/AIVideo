"""AiHubMix OpenAI-compatible image generation (gpt-image-2 / gpt-image-1)."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def api_key() -> str:
    key = _env("AIHUBMIX_API_KEY")
    if not key:
        raise RuntimeError("缺少 AIHUBMIX_API_KEY")
    return key


def base_url() -> str:
    return _env("AIHUBMIX_BASE_URL", "https://aihubmix.com/v1").rstrip("/")


def model() -> str:
    return _env("AIHUBMIX_IMAGE_MODEL", "gpt-image-2")


def image_size() -> str:
    return _env("AIHUBMIX_IMAGE_SIZE", "1024x1536")


def image_quality() -> str:
    return _env("AIHUBMIX_IMAGE_QUALITY", "high")


def image_timeout() -> float:
    return float(_env("AIHUBMIX_IMAGE_TIMEOUT", "300"))


def _http_post(url: str, body: dict[str, Any], *, timeout: float, headers: dict[str, str] | None = None) -> dict[str, Any]:
    req_headers = {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
    }
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=req_headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {raw[:500]}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"非 JSON 响应: {raw[:300]}") from exc


def build_prompt(image_prompt: str, *, headline: str = "") -> str:
    """增强 slide 的 image_prompt，便于高质量竖屏封面。"""
    parts = [
        image_prompt.strip(),
        "Vertical portrait 9:16 aspect ratio for mobile short video.",
        "Cinematic lighting, high detail, editorial news illustration style.",
        "No text, no watermark, no logo, no letters on image.",
    ]
    if headline:
        parts.append(f"Theme context (do not render as text): {headline.strip()}")
    return " ".join(p for p in parts if p)


def generate_image(
    prompt: str,
    *,
    size: str | None = None,
    quality: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """调用 /v1/images/generations，返回 {b64_json, url, revised_prompt}。"""
    body: dict[str, Any] = {
        "model": model(),
        "prompt": prompt,
        "n": 1,
        "size": size or image_size(),
    }
    q = quality or image_quality()
    m = model()
    if not m.startswith("gpt-4o-image"):
        body["quality"] = q

    started = time.time()
    data = _http_post(
        f"{base_url()}/images/generations",
        body,
        timeout=timeout or image_timeout(),
    )
    item = (data.get("data") or [{}])[0]
    if not isinstance(item, dict):
        raise RuntimeError(f"生图响应异常: {json.dumps(data, ensure_ascii=False)[:400]}")

    result: dict[str, Any] = {
        "elapsed_s": round(time.time() - started, 1),
        "model": m,
        "revised_prompt": item.get("revised_prompt") or prompt,
    }
    if item.get("b64_json"):
        result["b64_json"] = item["b64_json"]
    if item.get("url"):
        result["url"] = item["url"]
    if not result.get("b64_json") and not result.get("url"):
        raise RuntimeError(f"生图无图片数据: {json.dumps(item, ensure_ascii=False)[:400]}")
    return result


def save_b64_image(b64_data: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(base64.b64decode(b64_data))
    return path


def upload_public(path: Path) -> str:
    """上传本地图片，返回公网 URL（供 Coze 云端下载）。"""
    backend = _env("AIVIDEO_IMAGE_UPLOAD", "catbox").lower()
    if backend in ("0", "none", "off", "skip"):
        raise RuntimeError("未启用公网上传（AIVIDEO_IMAGE_UPLOAD=catbox）")

    if backend == "catbox":
        return _upload_catbox(path)
    raise RuntimeError(f"未知 AIVIDEO_IMAGE_UPLOAD={backend!r}，支持: catbox")


def _upload_catbox(path: Path) -> str:
    boundary = f"----AIVideo{int(time.time() * 1000)}"
    filename = path.name
    content = path.read_bytes()
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="reqtype"\r\n\r\n'
        f"fileupload\r\n"
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="fileToUpload"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8") + content + f"\r\n--{boundary}--\r\n".encode("utf-8")

    req = urllib.request.Request(
        "https://catbox.moe/user/api.php",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        url = resp.read().decode("utf-8", errors="replace").strip()
    if not url.startswith("http"):
        raise RuntimeError(f"catbox 上传失败: {url[:200]}")
    return url
