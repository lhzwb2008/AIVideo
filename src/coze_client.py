#!/usr/bin/env python3
"""Call a deployed Coze vibe workflow (/run or /run/stream) and return final JSON."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


def stream_url(run_url: str) -> str:
    base = run_url.rstrip("/")
    if base.endswith("/run"):
        return f"{base}/stream"
    return f"{base}/run/stream"


def _headers(token: str, *, stream: bool = False) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    if stream:
        h["Accept"] = "text/event-stream"
    return h


def _extract_output(payload: dict[str, Any]) -> str:
    url = payload.get("output") or ""
    if url:
        return str(url)
    data = payload.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return ""
    if isinstance(data, dict):
        return str(data.get("output") or "")
    return ""


def _format_error(body: str, http_code: int | None = None) -> str:
    prefix = f"HTTP {http_code}: " if http_code else ""
    text = body.strip()
    if not text:
        return f"{prefix}空响应"
    if text.startswith("<!") or "504 Gateway" in text:
        return (
            f"{prefix}网关超时（504）。视频合成约需 5–8 分钟，同步 /run 易被 Tengine 截断。"
            "请设置 COZE_VIBE_USE_STREAM=1 并在 code.coze.cn 重新部署后重试。"
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return f"{prefix}{text[:300]}"

    detail = data.get("detail")
    if isinstance(detail, dict) and detail.get("error_message"):
        msg = str(detail["error_message"])
        if "视频合成失败" in msg or "ffmpeg" in msg.lower():
            return (
                f"{prefix}工作流运行时错误（video_clip / ffmpeg）:\n{msg[:1200]}\n\n"
                "请在 code.coze.cn 打开项目，让 AI 修复 graphs/nodes/video_clip_node.py："
                "检查输入图片/音频路径是否存在、ffmpeg 命令参数、字体 drawtext 滤镜；"
                "修复后试运行通过并重新部署。"
            )
        return f"{prefix}{msg[:1200]}"
    if isinstance(detail, str):
        return f"{prefix}{detail}"
    return f"{prefix}{json.dumps(data, ensure_ascii=False)[:500]}"


def _parse_sse_lines(lines: list[str]) -> dict[str, Any] | None:
    data_line = ""
    for line in lines:
        if line.startswith("data:"):
            data_line = line[5:].strip()
    if not data_line:
        return None
    try:
        return json.loads(data_line)
    except json.JSONDecodeError:
        return None


def call_stream(
    run_url: str,
    token: str,
    body: dict[str, Any],
    *,
    timeout: float = 900,
    on_progress=None,
) -> dict[str, Any]:
    url = stream_url(run_url)
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=_headers(token, stream=True),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            if "text/event-stream" not in content_type:
                raw = resp.read().decode("utf-8", errors="replace")
                try:
                    return json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(_format_error(raw, resp.status)) from exc

            block: list[str] = []
            done: dict[str, Any] | None = None
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if line == "":
                    event = _parse_sse_lines(block)
                    block = []
                    if not event:
                        continue
                    etype = event.get("type")
                    if etype in ("node_start", "node_end") and on_progress:
                        on_progress(event)
                    if etype == "done":
                        done = event
                    continue
                block.append(line)

            if done:
                output = done.get("output") or {}
                if isinstance(output, dict):
                    return {"run_id": done.get("run_id"), **output}
                return {"run_id": done.get("run_id"), "output": output}
            raise RuntimeError("流式响应结束但未收到 done 事件")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        if exc.code == 404:
            raise RuntimeError(
                "流式接口 /run/stream 不可用（404）。请在 code.coze.cn 重新部署工作流后再试。"
            ) from exc
        raise RuntimeError(_format_error(raw, exc.code)) from exc


def call_sync(
    run_url: str,
    token: str,
    body: dict[str, Any],
    *,
    timeout: float = 900,
) -> dict[str, Any]:
    req = urllib.request.Request(
        run_url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=_headers(token),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(_format_error(raw, exc.code)) from exc

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(_format_error(raw)) from exc


def run_workflow(
    run_url: str,
    token: str,
    body: dict[str, Any],
    *,
    use_stream: bool = True,
    timeout: float = 900,
    on_progress=None,
) -> dict[str, Any]:
    if use_stream:
        try:
            return call_stream(run_url, token, body, timeout=timeout, on_progress=on_progress)
        except RuntimeError as exc:
            if "404" in str(exc) or "/run/stream 不可用" in str(exc):
                print(f"  ⚠️  {exc}，回退同步 /run …", file=sys.stderr)
            else:
                raise
    return call_sync(run_url, token, body, timeout=timeout)


def load_script_payload(script_file: str, input_key: str) -> dict[str, Any]:
    with open(script_file, encoding="utf-8") as f:
        data = json.load(f)
    script = data.get("script", data)
    payload = json.dumps(script, ensure_ascii=False, separators=(",", ":"))
    return {input_key: payload}


def _progress(event: dict[str, Any]) -> None:
    etype = event.get("type")
    name = event.get("node_name") or "?"
    if etype == "node_start":
        print(f"  ▶ {name}", file=sys.stderr)
    elif etype == "node_end":
        ms = event.get("time_cost_ms")
        suffix = f" ({ms}ms)" if ms is not None else ""
        print(f"  ✓ {name}{suffix}", file=sys.stderr)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Call Coze deployed workflow")
    parser.add_argument("script_file")
    parser.add_argument("--input-key", default=os.environ.get("COZE_VIBE_INPUT_KEY", "input"))
    parser.add_argument("--run-url", default=os.environ.get("COZE_VIBE_RUN_URL", ""))
    parser.add_argument("--token", default=os.environ.get("COZE_VIBE_API_TOKEN", ""))
    parser.add_argument(
        "--use-stream",
        action=argparse.BooleanOptionalAction,
        default=os.environ.get("COZE_VIBE_USE_STREAM", "1") != "0",
    )
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("COZE_VIBE_TIMEOUT", "900")))
    parser.add_argument("-o", "--output", default="logs/last_vibe_raw.json")
    args = parser.parse_args()

    if not args.run_url or not args.token:
        print("缺少 COZE_VIBE_RUN_URL 或 COZE_VIBE_API_TOKEN", file=sys.stderr)
        return 1

    body = load_script_payload(args.script_file, args.input_key)
    mode = "stream" if args.use_stream else "sync"
    print(f"  模式: {mode} ({stream_url(args.run_url) if args.use_stream else args.run_url})", file=sys.stderr)

    try:
        result = run_workflow(
            args.run_url,
            args.token,
            body,
            use_stream=args.use_stream,
            timeout=args.timeout,
            on_progress=_progress,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    if not _extract_output(result):
        print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
        detail = result.get("detail") or {}
        if isinstance(detail, dict) and detail.get("error_message"):
            print("\n错误:", detail["error_message"], file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
