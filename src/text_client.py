"""AiHubMix OpenAI-compatible chat completions（用于中间判断/筛选逻辑，默认 Claude Opus 4.8）。

约定：调研找文章 / 改编脚本 仍由 Cursor Cloud Agent 处理；本模块只做"中间判断"
（如从候选里挑最佳）的轻量调用。生图依然走 image_client.py。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
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


def text_model() -> str:
    """中间判断默认用 Claude Opus 4.8；可用 AIHUBMIX_TEXT_MODEL 覆盖。"""
    return _env("AIHUBMIX_TEXT_MODEL", "claude-opus-4-8")


def thinking_budget() -> int:
    """Claude thinking 模型的思考预算 token 数。
    Anthropic 文档要求最低 1024。设到最低省时间省钱。
    可用 AIHUBMIX_THINKING_BUDGET 覆盖；设为 0 表示不启用 thinking。
    """
    return int(_env("AIHUBMIX_THINKING_BUDGET", "1024"))


def reasoning_effort() -> str:
    """OpenAI 兼容接口的 reasoning_effort 字段，'low' 最快。"""
    return _env("AIHUBMIX_REASONING_EFFORT", "low")


def text_timeout() -> float:
    return float(_env("AIHUBMIX_TEXT_TIMEOUT", "600"))


_RETRY_HTTP_CODES = {408, 425, 429, 500, 502, 503, 504}


def chat_complete(
    *,
    system: str,
    user: str,
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int = 1024,
    response_format_json: bool = False,
) -> str:
    """单轮 chat completion，返回 assistant 文本。

    注意：Claude Opus 4.x thinking 模型 AiHubMix 端不接受 temperature / response_format，
    默认调用不带这两个参数；只有显式传入时才加上。
    """
    url = f"{base_url()}/chat/completions"
    body: dict[str, Any] = {
        "model": model or text_model(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        # 给思考型模型最少的思考量：OpenAI 兼容侧用 reasoning_effort
        "reasoning_effort": reasoning_effort(),
    }
    # Anthropic 原生 thinking 字段（AiHubMix 也透传）：min budget 1024
    budget = thinking_budget()
    if budget > 0:
        body["thinking"] = {"type": "enabled", "budget_tokens": max(1024, budget)}
    if temperature is not None:
        body["temperature"] = temperature
    if response_format_json:
        body["response_format"] = {"type": "json_object"}

    headers = {
        "Authorization": f"Bearer {api_key()}",
        "Content-Type": "application/json",
    }
    timeout = text_timeout()
    max_attempts = int(_env("AIHUBMIX_MAX_RETRIES", "4"))
    last_err: Exception | None = None

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
                    raise RuntimeError(f"chat 返回无 choices: {raw[:300]}")
                msg = choices[0].get("message") or {}
                content = msg.get("content")
                if isinstance(content, list):
                    content = "".join(
                        p.get("text", "") for p in content if isinstance(p, dict)
                    )
                if not isinstance(content, str) or not content.strip():
                    raise RuntimeError(f"chat 返回 content 为空: {raw[:300]}")
                try:
                    import cost_tracker
                    cost_tracker.record_text(data.get("usage"))
                except Exception:
                    pass
                return content
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            if e.code in _RETRY_HTTP_CODES and attempt < max_attempts:
                time.sleep(1.5 * attempt)
                last_err = RuntimeError(f"HTTP {e.code}: {err_body[:300]}")
                continue
            raise RuntimeError(f"AiHubMix chat HTTP {e.code}: {err_body[:500]}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < max_attempts:
                time.sleep(1.5 * attempt)
                continue
            raise RuntimeError(f"AiHubMix chat 网络失败: {e}") from e
    raise RuntimeError(f"AiHubMix chat 重试耗尽: {last_err}")
