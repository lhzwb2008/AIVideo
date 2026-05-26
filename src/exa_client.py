"""Exa AI 搜索 + 取全文（用于替代 Cursor Cloud Agent 联网部分）。

只暴露两个函数：
- search(query, ...)        → list[dict]，含 title/url/publishedDate/author/summary/highlights/text
- get_contents(urls, ...)   → list[dict]，含 url/title/text/author/publishedDate
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
    key = _env("EXA_API_KEY")
    if not key:
        raise RuntimeError("缺少 EXA_API_KEY")
    return key


def base_url() -> str:
    return _env("EXA_BASE_URL", "https://api.exa.ai").rstrip("/")


def timeout() -> float:
    return float(_env("EXA_TIMEOUT", "180"))


_RETRY_CODES = {408, 425, 429, 500, 502, 503, 504}


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{base_url()}{path}"
    headers = {
        "x-api-key": api_key(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    max_attempts = int(_env("EXA_MAX_RETRIES", "4"))
    to = timeout()
    last_err: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=to) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace") if e.fp else ""
            if e.code in _RETRY_CODES and attempt < max_attempts:
                time.sleep(1.5 * attempt)
                last_err = RuntimeError(f"HTTP {e.code}: {err[:300]}")
                continue
            raise RuntimeError(f"Exa {path} HTTP {e.code}: {err[:500]}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < max_attempts:
                time.sleep(1.5 * attempt)
                continue
            raise RuntimeError(f"Exa {path} 网络失败: {e}") from e
    raise RuntimeError(f"Exa {path} 重试耗尽: {last_err}")


def search(
    query: str,
    *,
    num_results: int = 15,
    start_published_date: str | None = None,
    end_published_date: str | None = None,
    category: str | None = None,
    include_text: bool = False,
    summary_query: str | None = None,
    highlights_sentences: int = 3,
    type_: str = "auto",
) -> list[dict]:
    """Exa /search with contents（默认带 summary + highlights，不要全文以省 token）。"""
    contents: dict[str, Any] = {
        "highlights": {"numSentences": highlights_sentences, "highlightsPerUrl": 2},
    }
    if summary_query:
        contents["summary"] = {"query": summary_query}
    else:
        contents["summary"] = True
    if include_text:
        contents["text"] = {"maxCharacters": 4000}

    body: dict[str, Any] = {
        "query": query,
        "numResults": num_results,
        "type": type_,
        "contents": contents,
    }
    if start_published_date:
        body["startPublishedDate"] = start_published_date
    if end_published_date:
        body["endPublishedDate"] = end_published_date
    if category:
        body["category"] = category

    data = _post("/search", body)
    return data.get("results") or []


def get_contents(
    urls: list[str],
    *,
    max_characters: int | None = None,
) -> list[dict]:
    """Exa /contents 取全文。urls 也可以是 Exa id。"""
    text_opt: Any = True
    if max_characters:
        text_opt = {"maxCharacters": max_characters}
    body: dict[str, Any] = {
        "urls": urls,
        "text": text_opt,
    }
    data = _post("/contents", body)
    return data.get("results") or []
