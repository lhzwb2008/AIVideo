"""AiHubMix OpenAI-compatible image generation (gpt-image-2 / gpt-image-1)."""

from __future__ import annotations

import base64
import json
import os
import sys
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


_RETRY_HTTP_CODES = {408, 412, 425, 429, 500, 502, 503, 504}


def _http_post(url: str, body: dict[str, Any], *, timeout: float, headers: dict[str, str] | None = None) -> dict[str, Any]:
    req_headers = {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
    }
    if headers:
        req_headers.update(headers)
    last_err: Exception | None = None
    max_attempts = int(os.environ.get("AIHUBMIX_MAX_RETRIES", "4"))
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=req_headers,
            method="POST",
        )
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            raw = resp.read().decode("utf-8", errors="replace")
            resp.close()
            return json.loads(raw)
        except BaseException as exc:
            etype = type(exc).__name__
            code = getattr(exc, "code", None)
            try:
                body_txt = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else ""
            except Exception:
                body_txt = ""
            print(
                f"  ⚠️  生图失败（第 {attempt}/{max_attempts}） {etype} code={code}: {(str(exc) or body_txt)[:300]}",
                file=sys.stderr,
                flush=True,
            )
            transient = (
                isinstance(exc, (TimeoutError, urllib.error.URLError, ConnectionError))
                or (code is not None and code in _RETRY_HTTP_CODES)
            )
            if transient and attempt < max_attempts:
                wait = min(20.0, 3.0 * attempt)
                print(f"  → {wait:.1f}s 后重试 …", file=sys.stderr, flush=True)
                time.sleep(wait)
                last_err = exc
                continue
            raise RuntimeError(f"{etype}({code}): {str(exc) or body_txt[:500]}") from exc
    raise RuntimeError(f"重试用尽: {last_err}")


def build_prompt(
    image_prompt: str,
    *,
    headline: str = "",
    chapter_title: str = "",
    on_image_text: list[str] | None = None,
    page_index: int = 0,
    total_pages: int = 5,
) -> str:
    """白板手绘科普风：方格纸 + 黑色钢笔线 + 中文手写注释。"""
    parts: list[str] = [
        "Hand-drawn whiteboard sketch on light beige graph paper, vertical portrait 9:16 aspect ratio.",
        "Black ballpoint pen line drawing, casual notebook illustration style, with subtle yellow and light purple highlighter accents.",
        "Crisp clean lines, comfortable amount of empty white space, friendly and educational mood.",
        "Important safe area for Douyin/TikTok UI: keep all meaningful text, logos, page numbers, and icons away from the top 18% of the canvas, the leftmost 8%, the rightmost 12%, and the bottom 25%. Use the middle 58% as the main information area, leaving generous empty graph-paper space above.",
        f"Page layout: {image_prompt.strip()}" if image_prompt.strip() else "",
    ]
    if chapter_title:
        parts.append(
            f"Place a small handwritten chapter tag in Chinese reading \"{chapter_title.strip()}\" near the upper-left of the safe area, around 18-22% from the top and 10-14% from the left, not at the extreme corner."
        )
    if page_index and total_pages:
        parts.append(
            f"Place a small page number handwritten as \"{page_index:02d}/{total_pages:02d}\" near the upper-right of the safe area, around 18-22% from the top and 14-18% from the right, not at the extreme corner."
        )
    labels = [str(t).strip() for t in (on_image_text or []) if str(t).strip()]
    if labels:
        joined = ", ".join(f"\"{t}\"" for t in labels)
        parts.append(
            "Render these EXACT Chinese handwritten labels naturally placed on the drawing as part of the diagram "
            f"(annotations, callouts, comparison labels): {joined}."
        )
        parts.append(
            "Use ONLY the listed Chinese labels above. Do not invent additional Chinese, English, or numeric text. "
            "Spelling must match exactly. Place labels with arrows / curly braces / underlines like a real notebook."
        )
    parts.append(
        "Bottom 22% of the canvas must be left as clean empty graph paper background (no drawing, no text), "
        "to leave room for subtitle and progress bar to be added later."
    )
    parts.append("No frames, no borders, no watermarks, no signatures, no logos.")
    if headline:
        parts.append(f"(Conceptual theme, do not render as text: {headline.strip()})")
    return " ".join(p for p in parts if p)


def build_cover_prompt(
    *,
    title: str,
    subtitle: str = "",
    keyword: str = "",
    doodle_hint: str = "",
) -> str:
    """开场封面海报：方格纸 + 手写大标题 + 简单装饰；标题字必须照搬。"""
    parts: list[str] = [
        "Hand-drawn whiteboard sketch on light beige graph paper, vertical portrait 9:16 aspect ratio.",
        "Black ballpoint pen line drawing, casual notebook illustration style, with subtle yellow and light purple highlighter accents.",
        "Composition: a bold handwritten CHINESE title fills the middle-upper portion of the page as the visual focal point.",
        f'The big handwritten Chinese title text must read EXACTLY: "{title.strip()}". '
        "Write it large in two lines if needed, with a hand-drawn yellow highlighter swipe underneath the most important keyword.",
    ]
    if subtitle.strip():
        parts.append(
            f'Below the title, a smaller handwritten Chinese subtitle reads EXACTLY: "{subtitle.strip()}". '
            "Subtitle is roughly 40% the size of the title, in plain pen, no highlight."
        )
    parts.append(
        "Around the title, sparse minimal hand-drawn doodles that hint at the topic — small icons, arrows, "
        "question marks, simple sketches. Plenty of empty graph paper space to breathe."
    )
    if doodle_hint.strip():
        parts.append(f"Doodle hint: {doodle_hint.strip()}.")
    elif keyword.strip():
        parts.append(f"Doodles should loosely reference the topic keyword: {keyword.strip()}.")
    parts.append(
        "Do NOT add any other Chinese, English, numbers, or random text besides the exact title and subtitle above. "
        "Spelling must match exactly character by character."
    )
    parts.append("No frames, no borders, no watermarks, no signatures, no logos, no photographic elements.")
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


