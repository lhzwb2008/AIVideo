"""粗略统计单次运行的 API 成本：Opus 文本 token + 生图次数/ token。

价格用环境变量覆盖（单位 USD）：
  OPUS_PRICE_INPUT_PER_M   每百万输入 token 价格（默认 15）
  OPUS_PRICE_OUTPUT_PER_M  每百万输出 token 价格（默认 75）
  IMAGE_PRICE_PER_IMAGE    每张图固定价（默认 0.0，若按 token 计则留 0）
  IMAGE_PRICE_INPUT_PER_M  生图输入 token 价（默认 5）
  IMAGE_PRICE_OUTPUT_PER_M 生图输出 token 价（默认 40）
默认价按 Anthropic Opus 公开价 + gpt-image 估算，仅供参考，请以 AiHubMix 实际账单为准。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

try:
    from paths import ROOT
except Exception:  # noqa: BLE001
    ROOT = Path(__file__).resolve().parent.parent

EVENTS_FILE = ROOT / "logs" / "api_cost.jsonl"

_lock = threading.Lock()

_state = {
    "text_calls": 0,
    "text_input_tokens": 0,
    "text_output_tokens": 0,
    "image_calls": 0,
    "image_input_tokens": 0,
    "image_output_tokens": 0,
    "usd_to_cny": float(os.environ.get("USD_TO_CNY", "7.2")),
}


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def reset() -> None:
    with _lock:
        for k in (
            "text_calls", "text_input_tokens", "text_output_tokens",
            "image_calls", "image_input_tokens", "image_output_tokens",
        ):
            _state[k] = 0


def _append_event(event: dict) -> None:
    """跨进程聚合：每次调用追加一行（生图在子进程里跑，父进程靠它汇总）。"""
    try:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def record_text(usage: dict | None) -> None:
    if not isinstance(usage, dict):
        usage = {}
    in_tok = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    out_tok = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    with _lock:
        _state["text_calls"] += 1
        _state["text_input_tokens"] += in_tok
        _state["text_output_tokens"] += out_tok
    _append_event({"ts": time.time(), "kind": "text", "in": in_tok, "out": out_tok})


def record_image(usage: dict | None) -> None:
    if not isinstance(usage, dict):
        usage = {}
    in_tok = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    with _lock:
        _state["image_calls"] += 1
        _state["image_input_tokens"] += in_tok
        _state["image_output_tokens"] += out_tok
    _append_event({"ts": time.time(), "kind": "image", "in": in_tok, "out": out_tok})


def snapshot() -> dict:
    with _lock:
        s = dict(_state)
    opus_in = _f("OPUS_PRICE_INPUT_PER_M", 15.0)
    opus_out = _f("OPUS_PRICE_OUTPUT_PER_M", 75.0)
    img_each = _f("IMAGE_PRICE_PER_IMAGE", 0.0)
    img_in = _f("IMAGE_PRICE_INPUT_PER_M", 5.0)
    img_out = _f("IMAGE_PRICE_OUTPUT_PER_M", 40.0)

    text_usd = (s["text_input_tokens"] / 1e6) * opus_in + (s["text_output_tokens"] / 1e6) * opus_out
    image_usd = s["image_calls"] * img_each
    image_usd += (s["image_input_tokens"] / 1e6) * img_in + (s["image_output_tokens"] / 1e6) * img_out
    total_usd = text_usd + image_usd
    rate = s["usd_to_cny"]
    return {
        **s,
        "text_usd": round(text_usd, 4),
        "image_usd": round(image_usd, 4),
        "total_usd": round(total_usd, 4),
        "total_cny": round(total_usd * rate, 3),
    }


def _price(counts: dict) -> dict:
    opus_in = _f("OPUS_PRICE_INPUT_PER_M", 15.0)
    opus_out = _f("OPUS_PRICE_OUTPUT_PER_M", 75.0)
    img_each = _f("IMAGE_PRICE_PER_IMAGE", 0.0)
    img_in = _f("IMAGE_PRICE_INPUT_PER_M", 5.0)
    img_out = _f("IMAGE_PRICE_OUTPUT_PER_M", 40.0)
    text_usd = (counts["text_input_tokens"] / 1e6) * opus_in + (counts["text_output_tokens"] / 1e6) * opus_out
    image_usd = counts["image_calls"] * img_each
    image_usd += (counts["image_input_tokens"] / 1e6) * img_in + (counts["image_output_tokens"] / 1e6) * img_out
    total_usd = text_usd + image_usd
    rate = float(os.environ.get("USD_TO_CNY", "7.2"))
    return {
        **counts,
        "text_usd": round(text_usd, 4),
        "image_usd": round(image_usd, 4),
        "total_usd": round(total_usd, 4),
        "total_cny": round(total_usd * rate, 3),
    }


def aggregate_since(start_ts: float) -> dict:
    """从事件文件聚合 start_ts 之后的所有调用（含子进程生图），并算价。"""
    counts = {
        "text_calls": 0, "text_input_tokens": 0, "text_output_tokens": 0,
        "image_calls": 0, "image_input_tokens": 0, "image_output_tokens": 0,
    }
    try:
        with EVENTS_FILE.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if float(ev.get("ts") or 0) < start_ts:
                    continue
                if ev.get("kind") == "text":
                    counts["text_calls"] += 1
                    counts["text_input_tokens"] += int(ev.get("in") or 0)
                    counts["text_output_tokens"] += int(ev.get("out") or 0)
                elif ev.get("kind") == "image":
                    counts["image_calls"] += 1
                    counts["image_input_tokens"] += int(ev.get("in") or 0)
                    counts["image_output_tokens"] += int(ev.get("out") or 0)
    except FileNotFoundError:
        pass
    return _price(counts)


def report_window(start_ts: float, *, videos: int = 0, prefix: str = "") -> str:
    s = aggregate_since(start_ts)
    lines = [
        f"{prefix}成本估算（仅供参考，以 AiHubMix 实际账单为准）：",
        f"  文本(Opus)：{s['text_calls']} 次，"
        f"输入 {s['text_input_tokens']:,} tok / 输出 {s['text_output_tokens']:,} tok"
        f" ≈ ${s['text_usd']:.4f}",
        f"  生图：{s['image_calls']} 张，"
        f"输入 {s['image_input_tokens']:,} tok / 输出 {s['image_output_tokens']:,} tok"
        f" ≈ ${s['image_usd']:.4f}",
        f"  合计：≈ ${s['total_usd']:.4f}（约 ¥{s['total_cny']:.2f}）",
    ]
    if videos > 0:
        lines.append(
            f"  平均每条视频：≈ ${s['total_usd'] / videos:.4f}（约 ¥{s['total_cny'] / videos:.2f}）"
        )
    return "\n".join(lines)


def report(prefix: str = "") -> str:
    s = snapshot()
    lines = [
        f"{prefix}成本估算（仅供参考，以实际账单为准）：",
        f"  文本(Opus)：{s['text_calls']} 次调用，"
        f"输入 {s['text_input_tokens']:,} tok / 输出 {s['text_output_tokens']:,} tok"
        f" ≈ ${s['text_usd']:.4f}",
        f"  生图：{s['image_calls']} 张，"
        f"输入 {s['image_input_tokens']:,} tok / 输出 {s['image_output_tokens']:,} tok"
        f" ≈ ${s['image_usd']:.4f}",
        f"  合计：≈ ${s['total_usd']:.4f}（约 ¥{s['total_cny']:.2f}）",
    ]
    return "\n".join(lines)
