"""粗略统计单次运行的 AiHubMix 成本，按实际调用的模型计价。

文本单价（美元 / 百万 token，AiHubMix 公示价）：
  claude-opus-5-5    输入 4 / 输出 20
  claude-sonnet-5-5  输入 2 / 输出 10
  grok-4.7           输入 2 / 输出 6
名字里带 opus / sonnet / grok 但没单独列价的，按上面三档估算。
其余未知模型用 OPUS_PRICE_INPUT_PER_M / OPUS_PRICE_OUTPUT_PER_M（默认 4 / 20）。
生图按 IMAGE_PRICE_INPUT_PER_M / IMAGE_PRICE_OUTPUT_PER_M（默认 5 / 40）的 token 价估算。
写稿用的 Cursor 不经过 AiHubMix，不计入本账单。
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
        _state["_text"] = {}
        _state["_image"] = {}


def _append_event(event: dict) -> None:
    """跨进程聚合：每次调用追加一行（生图在子进程里跑，父进程靠它汇总）。"""
    try:
        EVENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with EVENTS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


# AiHubMix 公示价：美元 / 百万 token（输入, 输出）
_TEXT_RATES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "grok-4.7": (2.0, 6.0),
}


def _text_rates(model: str) -> tuple[float, float]:
    key = (model or "").strip()
    if key in _TEXT_RATES:
        return _TEXT_RATES[key]
    low = key.lower()
    if "grok" in low:
        return _TEXT_RATES["grok-4.7"]
    if "sonnet" in low:
        return _TEXT_RATES["claude-sonnet-5-5"]
    return (_f("OPUS_PRICE_INPUT_PER_M", 4.0), _f("OPUS_PRICE_OUTPUT_PER_M", 20.0))


def _tokens(usage: dict | None) -> tuple[int, int]:
    usage = usage if isinstance(usage, dict) else {}
    in_tok = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    out_tok = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    return in_tok, out_tok


def _bump(bucket: dict, n_in: int, n_out: int) -> None:
    bucket["calls"] = int(bucket.get("calls") or 0) + 1
    bucket["in"] = int(bucket.get("in") or 0) + n_in
    bucket["out"] = int(bucket.get("out") or 0) + n_out


def record_text(usage: dict | None, model: str = "") -> None:
    in_tok, out_tok = _tokens(usage)
    name = model.strip() or os.environ.get("AIHUBMIX_TEXT_MODEL", "claude-opus-5-5")
    with _lock:
        bucket = _state.setdefault("_text", {}).setdefault(name, {})
        _bump(bucket, in_tok, out_tok)
        _state["text_calls"] += 1
        _state["text_input_tokens"] += in_tok
        _state["text_output_tokens"] += out_tok
    _append_event({"ts": time.time(), "kind": "text", "model": name, "in": in_tok, "out": out_tok})


def record_image(usage: dict | None, model: str = "") -> None:
    in_tok, out_tok = _tokens(usage)
    name = model.strip() or os.environ.get("AIHUBMIX_IMAGE_MODEL", "")
    with _lock:
        bucket = _state.setdefault("_image", {})
        _bump(bucket, in_tok, out_tok)
        if name:
            bucket["model"] = name
        _state["image_calls"] += 1
        _state["image_input_tokens"] += in_tok
        _state["image_output_tokens"] += out_tok
    _append_event({"ts": time.time(), "kind": "image", "model": name, "in": in_tok, "out": out_tok})


def _image_usd(calls: int, n_in: int, n_out: int) -> float:
    return (
        calls * _f("IMAGE_PRICE_PER_IMAGE", 0.0)
        + (n_in / 1e6) * _f("IMAGE_PRICE_INPUT_PER_M", 5.0)
        + (n_out / 1e6) * _f("IMAGE_PRICE_OUTPUT_PER_M", 40.0)
    )


def _render(text: dict, image: dict, *, videos: int, prefix: str) -> str:
    lines = [f"{prefix}成本估算（仅供参考，以 AiHubMix 实际账单为准）："]
    total = 0.0
    for name, bucket in text.items():
        rin, rout = _text_rates(name)
        usd = bucket["in"] / 1e6 * rin + bucket["out"] / 1e6 * rout
        total += usd
        lines.append(
            f"  文本 {name}：{bucket['calls']} 次，"
            f"输入 {bucket['in']:,} tok / 输出 {bucket['out']:,} tok"
            f" ≈ ${usd:.4f}（${rin:g}/${rout:g} 每百万）"
        )
    if not text:
        lines.append("  文本：0 次")
    img_calls = int(image.get("calls") or 0)
    img_usd = _image_usd(img_calls, int(image.get("in") or 0), int(image.get("out") or 0))
    total += img_usd
    img_name = str(image.get("model") or "").strip()
    label = f"生图 {img_name}" if img_name else "生图"
    if img_calls:
        lines.append(
            f"  {label}：{img_calls} 张，"
            f"输入 {int(image.get('in') or 0):,} tok / 输出 {int(image.get('out') or 0):,} tok"
            f" ≈ ${img_usd:.4f}"
        )
    rate = float(os.environ.get("USD_TO_CNY", "7.2"))
    lines.append(f"  合计：≈ ${total:.4f}（约 ¥{total * rate:.2f}）")
    if videos > 0 and total:
        lines.append(f"  平均每条视频：≈ ${total / videos:.4f}（约 ¥{total * rate / videos:.2f}）")
    lines.append("  写稿走 Cursor，不在这笔账单里")
    return "\n".join(lines)


def aggregate_since(start_ts: float) -> dict:
    """从事件文件聚合 start_ts 之后的所有调用（含子进程生图）。"""
    text: dict[str, dict] = {}
    image = {"calls": 0, "in": 0, "out": 0, "model": ""}
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
                    name = str(ev.get("model") or os.environ.get("AIHUBMIX_TEXT_MODEL") or "claude-opus-5-5")
                    _bump(text.setdefault(name, {}), int(ev.get("in") or 0), int(ev.get("out") or 0))
                elif ev.get("kind") == "image":
                    _bump(image, int(ev.get("in") or 0), int(ev.get("out") or 0))
                    if ev.get("model"):
                        image["model"] = str(ev["model"])
    except FileNotFoundError:
        pass
    return {
        "text": text,
        "image": image,
        "text_calls": sum(int(b["calls"]) for b in text.values()),
        "image_calls": int(image["calls"]),
    }


def report_window(start_ts: float, *, videos: int = 0, prefix: str = "") -> str:
    s = aggregate_since(start_ts)
    return _render(s["text"], s["image"], videos=videos, prefix=prefix)


def report(prefix: str = "") -> str:
    with _lock:
        text = {k: dict(v) for k, v in _state.get("_text", {}).items()}
        image = dict(_state.get("_image", {}))
    return _render(text, image, videos=0, prefix=prefix)
