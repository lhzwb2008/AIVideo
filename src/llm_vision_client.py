"""多模态 chat（浏览器发布 LLM 兜底）。

默认 LLM_BROWSER_MODEL=claude-opus-4-8（AiHubMix 视觉）。
步数：Opus 默认 20，Qwen 默认 30（LLM_BROWSER_MAX_STEPS 可覆盖）。
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from text_client import api_key, base_url, text_timeout

_RETRY_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def browser_model() -> str:
    return (
        _env("LLM_BROWSER_MODEL")
        or _env("AIHUBMIX_TEXT_MODEL")
        or "claude-opus-4-8"
    )


def _model_uses_dashscope(model: str) -> bool:
    return "qwen" in model.lower()


def browser_max_steps() -> int:
    raw = _env("LLM_BROWSER_MAX_STEPS")
    if raw:
        return max(1, int(raw))
    if _model_uses_dashscope(browser_model()):
        return 30
    return 20


def browser_provider_label() -> str:
    return "DashScope" if _model_uses_dashscope(browser_model()) else "AiHubMix"


def llm_vision_available() -> bool:
    model = browser_model()
    if _model_uses_dashscope(model):
        return bool(_env("DASHSCOPE_API_KEY"))
    return bool(_env("AIHUBMIX_API_KEY"))


def _vision_request_config(model: str) -> tuple[str, str, int]:
    if _model_uses_dashscope(model):
        key = _env("DASHSCOPE_API_KEY")
        if not key:
            raise RuntimeError("缺少 DASHSCOPE_API_KEY（Qwen 视觉模型）")
        base = _env(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ).rstrip("/")
        timeout = int(_env("DASHSCOPE_TIMEOUT", _env("AIHUBMIX_TEXT_TIMEOUT", "120")))
        return key, f"{base}/chat/completions", timeout
    return api_key(), f"{base_url()}/chat/completions", text_timeout()


def _image_part(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    b64 = base64.standard_b64encode(data).decode("ascii")
    suffix = path.suffix.lower()
    mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else "image/png"
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}"},
    }


def vision_chat(
    *,
    system: str,
    user_text: str,
    screenshot: Path | None = None,
    model: str | None = None,
    max_tokens: int = 280,
) -> str:
    user_content: str | list[dict[str, Any]]
    if screenshot and screenshot.is_file():
        user_content = [
            {"type": "text", "text": user_text},
            _image_part(screenshot),
        ]
    else:
        user_content = user_text

    body: dict[str, Any] = {
        "model": model or browser_model(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "max_tokens": max_tokens,
    }

    model_name = str(body["model"])
    api_key_val, url, timeout = _vision_request_config(model_name)
    headers = {
        "Authorization": f"Bearer {api_key_val}",
        "Content-Type": "application/json",
    }
    max_attempts = int(_env("AIHUBMIX_MAX_RETRIES", "3"))
    last_err: Exception | None = None
    provider = "DashScope" if _model_uses_dashscope(model_name) else "AiHubMix"

    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                data = json.loads(raw)
                choices = data.get("choices") or []
                if not choices:
                    raise RuntimeError(f"vision chat 无 choices: {raw[:300]}")
                msg = choices[0].get("message") or {}
                content_out = msg.get("content")
                if isinstance(content_out, list):
                    content_out = "".join(
                        p.get("text", "") for p in content_out if isinstance(p, dict)
                    )
                if not isinstance(content_out, str) or not content_out.strip():
                    raise RuntimeError(f"vision chat content 为空: {raw[:300]}")
                try:
                    import cost_tracker

                    cost_tracker.record_text(data.get("usage"))
                except Exception:
                    pass
                return content_out
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            if e.code in _RETRY_HTTP_CODES and attempt < max_attempts:
                time.sleep(1.5 * attempt)
                last_err = RuntimeError(f"HTTP {e.code}: {err_body[:300]}")
                continue
            raise RuntimeError(f"{provider} chat HTTP {e.code}: {err_body[:500]}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < max_attempts:
                time.sleep(1.5 * attempt)
                continue
            raise RuntimeError(f"{provider} chat 网络失败: {e}") from e
    raise RuntimeError(f"{provider} chat 重试耗尽: {last_err}")


def parse_json_response(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    action = re.search(r'"action"\s*:\s*"([a-z_]+)"', text, re.I)
    thought = re.search(r'"thought"\s*:\s*"([^"]{0,200})"', text, re.I)
    ref = re.search(r'"ref"\s*:\s*(\d+)', text)
    wait = re.search(r'"wait_seconds"\s*:\s*([\d.]+)', text)
    if action:
        out: dict[str, Any] = {
            "action": action.group(1).lower(),
            "thought": thought.group(1) if thought else "",
        }
        if ref:
            out["ref"] = int(ref.group(1))
        if wait:
            out["wait_seconds"] = float(wait.group(1))
        return out
    raise ValueError(f"无法解析模型 JSON: {text[:400]}")
