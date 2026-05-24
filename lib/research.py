#!/usr/bin/env python3
"""本地 Cursor Agent 调研：每日锁定一个 AI 热点，基于精华文章深度解读，输出 Coze 合成 JSON。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.cursor_client import create_agent, create_run, run_with_stream  # noqa: E402

RESEARCH_PROMPT = """你是 AI 深度解读编导。请联网完成以下任务，输出 60 秒竖屏短视频脚本。

【内容策略 · 必须遵守】
1. 先搜索全网（48 小时内），**只选 1 个** 当前最热的 AI 话题/关键词（如 OpenAI、Claude、Gemini、Sora、Agent 等具体热点）。
2. 围绕该话题再找 **1 篇** 你认为最精华的深度文章/报道（优先权威媒体、官方博客、高质量分析），**阅读原文观点**。
3. **不要输出你自己的观点**；口播与 PPT 只复述、压缩该文的论点、事实与结论，可注明「文章认为…」。
4. 视频 `title` 和第 1 页 headline **必须突出该热点关键词**，方便用户在抖音搜索该词时命中（title 建议：关键词 + 短卖点，15–22 字）。
5. `keyword` 与 title、第 1 页 headline 须指向同一热点，写法可一致（推荐）或仅差空格/连写（如 `Gemini Omni` 与 `GeminiOmni`）。

【分工】你写全部文案；Coze 原样上屏/配音。PPT = narration 的可视化概要，禁止两套说法。

【写作顺序 · 每页】
1. 先写 narration（第 1/5 页 40–50 字；第 2–4 页 55–75 字深入展开；第 5 页 45–55 字收束）
2. 再写 headline（含本页核心关键词，是 narration 的标题化总结）
3. 再写 bullets×3（拆分 narration 已有信息，禁止模糊代称）

【5 页结构 · 每页 layout 不同，禁止 5 页都用「标题+3条圆角bullet」模板】

| 页 | layout | 画面形态 | bullets |
|----|--------|----------|---------|
| 1 | cover | **封面大标题**：超大 headline + subtitle，像短视频封面，不要列表 | 0 条（不要 bullet） |
| 2 | insight | **观点页**：中等标题 + 2 条要点，左右或上下大块，不要编号 pill | 2 条 |
| 3 | data | **数据页**：中央超大 stat（如「600亿美元」）+ 短说明 | 1–2 条 |
| 4 | story | **叙事页**：2–3 条要点用时间线/折线连接，不要和 insight 同款 | 2–3 条 |
| 5 | outro | **收束页**：一句 takeaway + 关键词标签，不要编号列表 | 0–2 条 |

【封面页 cover · 最重要】
- headline = 与根节点 title 相同或极接近的**完整新闻标题**（含 keyword）
- subtitle = 12–18 字副标题（文章出处/悬念，如「Euronews 深度解读 · 或刷新 IPO 纪录」）
- bullets = []（空数组）
- 禁止在封面放 3 条 bullet、禁止「头条」小标签、禁止「热点速览 Agent 大模型」等模板字样

【输出】只输出一个 JSON，不要 markdown，不要解释，不要重复两份 JSON。

JSON 结构：
{{
  "title": "OpenAI冲刺IPO：拟募600亿美元",
  "keyword": "OpenAI",
  "source": {{ "title": "...", "url": "https://...", "site": "Euronews" }},
  "slides": [
    {{
      "layout": "cover",
      "headline": "OpenAI冲刺IPO：拟募600亿美元",
      "subtitle": "Euronews 五问读懂史上最大 IPO 争夺",
      "bullets": [],
      "narration": "口播…",
      "image_prompt": "English, vertical 9:16 cinematic cover, no text"
    }},
    {{
      "layout": "insight",
      "headline": "…",
      "bullets": [{{"title":"…","desc":"…"}}, {{"title":"…","desc":"…"}}],
      "narration": "…",
      "image_prompt": "…"
    }},
    {{
      "layout": "data",
      "headline": "…",
      "stat": "600亿美元",
      "bullets": [{{"title":"…","desc":"…"}}],
      "narration": "…",
      "image_prompt": "…"
    }},
    {{
      "layout": "story",
      "headline": "…",
      "bullets": […],
      "narration": "…",
      "image_prompt": "…"
    }},
    {{
      "layout": "outro",
      "headline": "…",
      "bullets": [{{"title":"#OpenAI","desc":""}}],
      "narration": "…",
      "image_prompt": "…"
    }}
  ]
}}

用户输入的检索方向：{topic}（可忽略字面，以当日全网最热 AI 单话题为准）
"""

# 5 页固定 layout 顺序
_SLIDE_LAYOUTS = ("cover", "insight", "data", "story", "outro")


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


def extract_json(text: str) -> dict:
    text = text.strip()
    if not text:
        raise ValueError("Agent 返回为空")

    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()

    decoder = json.JSONDecoder()
    candidates: list[dict] = []
    idx = 0
    while idx < len(text):
        brace = text.find("{", idx)
        if brace < 0:
            break
        try:
            obj, end = decoder.raw_decode(text, brace)
            if isinstance(obj, dict) and "title" in obj and "slides" in obj:
                candidates.append(obj)
            idx = end
        except json.JSONDecodeError:
            idx = brace + 1

    if candidates:
        return candidates[0]

    start = text.find("{")
    if start >= 0:
        obj, _ = decoder.raw_decode(text, start)
        if isinstance(obj, dict):
            return obj

    raise ValueError("无法从 Agent 回复中解析 JSON")


def validate_script(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("根节点必须是 object")
    for key in ("title", "keyword", "slides", "source"):
        if key not in data:
            raise ValueError(f"缺少 {key}")
    keyword = str(data["keyword"]).strip()
    if len(keyword) < 2:
        raise ValueError("keyword 太短，需为可搜索的热点词")

    source = data.get("source") or {}
    if not source.get("url") or not str(source["url"]).startswith("http"):
        raise ValueError("source.url 必须是有效的原文链接")
    if not source.get("title"):
        raise ValueError("缺少 source.title（参考文章标题）")

    slides = data["slides"]
    if not isinstance(slides, list) or len(slides) != 5:
        raise ValueError(f"slides 必须恰好 5 页，当前 {len(slides) if isinstance(slides, list) else '非数组'}")
    for i, slide in enumerate(slides):
        expected = _SLIDE_LAYOUTS[i]
        layout = slide.get("layout") or expected
        if layout != expected:
            raise ValueError(f"第 {i + 1} 页 layout 应为 {expected}，当前 {layout}")
        slide["layout"] = layout
        if not slide.get("headline") or not slide.get("narration") or not slide.get("image_prompt"):
            raise ValueError(f"第 {i + 1} 页缺少 headline/narration/image_prompt")
        bullets = slide.get("bullets") or []
        _validate_layout_fields(i, layout, slide, bullets)
    return data


def _validate_layout_fields(index: int, layout: str, slide: dict, bullets: list) -> None:
    if layout == "cover":
        if bullets:
            raise ValueError("封面页 cover 的 bullets 必须为空 []")
        if not slide.get("subtitle"):
            raise ValueError("封面页须含 subtitle 副标题")
    elif layout == "insight":
        if not (2 <= len(bullets) <= 3):
            raise ValueError("insight 页 bullets 须 2–3 条")
    elif layout == "data":
        if not slide.get("stat"):
            raise ValueError("data 页须含 stat 大数字字段")
        if not (1 <= len(bullets) <= 2):
            raise ValueError("data 页 bullets 须 1–2 条")
    elif layout == "story":
        if not (2 <= len(bullets) <= 3):
            raise ValueError("story 页 bullets 须 2–3 条")
    elif layout == "outro":
        if len(bullets) > 2:
            raise ValueError("outro 页 bullets 最多 2 条")


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="Cursor Agent 本地调研 → JSON 脚本")
    parser.add_argument("topic", nargs="?", default=os.environ.get("COZE_WORKFLOW_TOPIC", "今日AI新闻"))
    parser.add_argument("-o", "--output", default=str(ROOT / "logs" / "last_script.json"))
    parser.add_argument("--agent-id", help="复用已有 agent 追问")
    args = parser.parse_args()

    prompt = RESEARCH_PROMPT.format(topic=args.topic)
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/2] Cursor 调研: {args.topic}")
    print(f"  model={os.environ.get('CURSOR_MODEL_ID', 'composer-2')}")

    def on_tool(payload: dict) -> None:
        name = str(payload.get("name") or payload.get("tool") or "")
        if re.search(r"search|web", name, re.I):
            print("  🔍 联网搜索中…")

    if args.agent_id:
        agent_id = args.agent_id
        run_id = create_run(agent_id, prompt)
    else:
        agent_id, run_id = create_agent(prompt)

    text, status = run_with_stream(agent_id, run_id, on_tool_call=on_tool)
    print(f"  agent={agent_id} run={run_id} status={status}")

    if status != "FINISHED":
        print(text or "(无输出)", file=sys.stderr)
        return 1

    try:
        script = validate_script(extract_json(text))
    except (ValueError, json.JSONDecodeError) as e:
        raw_path = logs_dir / "last_research_raw.txt"
        raw_path.write_text(text, encoding="utf-8")
        print(f"脚本校验失败: {e}", file=sys.stderr)
        print(f"原始回复已保存: {raw_path}", file=sys.stderr)
        return 1

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "topic": args.topic,
        "agent_id": agent_id,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "script": script,
    }
    out_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    agent_store = logs_dir / "cursor_agent.json"
    agent_store.write_text(json.dumps({"agent_id": agent_id}, indent=2), encoding="utf-8")

    print(f"[2/2] 脚本已保存: {out_path}")
    print(f"  关键词={script.get('keyword')} title={script['title']} slides={len(script['slides'])}")
    src = script.get("source") or {}
    if src.get("url"):
        print(f"  参考: {src.get('site', '')} {src.get('title', '')[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
