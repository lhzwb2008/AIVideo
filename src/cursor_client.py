"""Cursor Cloud Agents REST client (参考 workclaw/src/cursorClient.ts)。"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def auth_header() -> str:
    key = _env("CURSOR_API_KEY")
    if not key:
        raise RuntimeError("缺少 CURSOR_API_KEY（https://cursor.com/dashboard/integrations）")
    token = base64.b64encode(f"{key}:".encode()).decode()
    return f"Basic {token}"


def base_url() -> str:
    return _env("CURSOR_BASE_URL", "https://api.cursor.com").rstrip("/")


def model_id() -> str:
    # 财经写稿/联网叙事优先 Grok；仍可用 .env 覆盖回 composer-2.5
    return _env("CURSOR_MODEL_ID", "cursor-grok-4.5-high")


def sandbox_repo_url() -> str:
    url = _env("CURSOR_SANDBOX_REPO_URL")
    if not url:
        raise RuntimeError("缺少 CURSOR_SANDBOX_REPO_URL（Agent 挂载的 GitHub 仓库 URL）")
    return url


def _http(method: str, path: str, body: dict | None = None) -> tuple[int, Any, str]:
    url = base_url() + path
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": auth_header(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    last_err: Exception | None = None
    for attempt in range(1, 5):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode()
                try:
                    parsed = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    parsed = None
                return resp.status, parsed, raw
        except urllib.error.HTTPError as e:
            raw = e.read().decode() if e.fp else ""
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = None
            return e.code, parsed, raw
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(0.5 * attempt)
    raise RuntimeError(f"HTTP {method} {path} 失败: {last_err}") from last_err


def create_agent(prompt: str) -> tuple[str, str]:
    status, data, raw = _http(
        "POST",
        "/v1/agents",
        {
            "prompt": {"text": prompt},
            "model": {"id": model_id()},
            "repos": [{"url": sandbox_repo_url()}],
            "autoCreatePR": False,
        },
    )
    if status not in (200, 201) or not isinstance(data, dict):
        raise RuntimeError(f"createAgent 失败 {status}: {raw}")
    agent_id = data["agent"]["id"]
    run_id = data["run"]["id"]
    return agent_id, run_id


def create_run(agent_id: str, prompt: str) -> str:
    for _ in range(30):
        status, data, raw = _http(
            "POST",
            f"/v1/agents/{agent_id}/runs",
            {"prompt": {"text": prompt}},
        )
        if status in (200, 201) and isinstance(data, dict):
            return data["run"]["id"]
        if status == 409:
            time.sleep(2)
            continue
        raise RuntimeError(f"createRun 失败 {status}: {raw}")
    raise RuntimeError(f"createRun: agent {agent_id} 一直 busy")


def get_run(agent_id: str, run_id: str) -> dict:
    status, data, raw = _http("GET", f"/v1/agents/{agent_id}/runs/{run_id}")
    if status != 200 or not isinstance(data, dict):
        raise RuntimeError(f"getRun 失败 {status}: {raw}")
    return data


def _consume_sse(
    agent_id: str,
    run_id: str,
    on_assistant: Callable[[str], None] | None = None,
    on_tool_call: Callable[[dict], None] | None = None,
    timeout_s: float = 600,
) -> None:
    url = f"{base_url()}/v1/agents/{agent_id}/runs/{run_id}/stream"
    req = urllib.request.Request(
        url,
        headers={"Authorization": auth_header(), "Accept": "text/event-stream"},
    )
    deadline = time.time() + timeout_s
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            buf = ""
            while time.time() < deadline:
                chunk = resp.read(4096)
                if not chunk:
                    break
                buf += chunk.decode(errors="replace")
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    event_name = "message"
                    data_lines: list[str] = []
                    for line in block.split("\n"):
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                        elif line.startswith("data:"):
                            data_lines.append(line[5:].strip())
                    if not data_lines:
                        continue
                    try:
                        payload = json.loads("\n".join(data_lines))
                    except json.JSONDecodeError:
                        continue
                    if event_name == "assistant" and on_assistant:
                        text = payload.get("text")
                        if isinstance(text, str) and text:
                            on_assistant(text)
                    elif event_name == "tool_call" and on_tool_call:
                        on_tool_call(payload)
                    elif event_name in ("result", "done", "error"):
                        return
    except Exception:
        return


def run_with_stream(
    agent_id: str,
    run_id: str,
    *,
    timeout_ms: int = int(os.environ.get("CURSOR_AGENT_TIMEOUT_MS", "1500000")),
    poll_interval_ms: int = 4000,
    on_assistant: Callable[[str], None] | None = None,
    on_tool_call: Callable[[dict], None] | None = None,
) -> tuple[str, str]:
    assistant_buf: list[str] = []

    def _on_assistant(delta: str) -> None:
        assistant_buf.append(delta)
        if on_assistant:
            on_assistant(delta)

    import threading

    sse_thread = threading.Thread(
        target=_consume_sse,
        kwargs={
            "agent_id": agent_id,
            "run_id": run_id,
            "on_assistant": _on_assistant,
            "on_tool_call": on_tool_call,
            "timeout_s": timeout_ms / 1000,
        },
        daemon=True,
    )
    sse_thread.start()

    deadline = time.time() + timeout_ms / 1000
    final_status = "TIMEOUT"
    final_text = ""
    while time.time() < deadline:
        try:
            r = get_run(agent_id, run_id)
        except Exception:
            time.sleep(poll_interval_ms / 1000)
            continue
        status = r.get("status", "")
        if status in ("FINISHED", "ERROR", "CANCELLED"):
            final_status = status
            final_text = r.get("result") or ""
            break
        time.sleep(poll_interval_ms / 1000)

    sse_thread.join(timeout=2)
    text = final_text or "".join(assistant_buf)
    return text, final_status
