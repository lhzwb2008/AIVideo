#!/usr/bin/env python3
"""本地 Cursor Agent 调研：每日锁定一个大众向 AI 热点，口语化口播脚本，输出 Coze 合成 JSON。"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from cursor_client import create_agent, create_run, run_with_stream
from paths import ROOT

RESEARCH_PROMPT = """你是「抖音 AI 资讯」口播编剧。请联网完成以下任务，输出 60 秒竖屏短视频脚本。
目标观众是刷短视频的普通用户（非投资人、非研究员），风格像科技区 UP 主给朋友讲新闻。

【选题 · 必须遵守】
1. 先搜索全网（{days} 天内），列出 2–3 个 AI 热点候选，**只选 1 个**最终话题。
2. 对每个候选自评三项（各 0–3 分，满分 9）：大众认知度、情绪钩子（震惊/利好/翻车/免费/涨价）、一句话讲清。
3. **总分 ≥ 7 才选**；若最热话题太专业（IPO 交表细节、论文术语、监管黑话、纯融资八卦），改选「次热但更大众」的。
4. 优先：产品发布、功能更新、免费/涨价、名人冲突、明显翻车、能 3 秒讲清的大新闻。
5. 围绕选定话题找 **1 篇** 权威报道/官方博客，**数字、日期、公司动作必须来自该文**，禁止编造。
{exclude_section}{batch_section}

【事实与文风】
- 可口语转述、比喻、轻微调侃；事实不能改、不能编。
- 全片「文章认为/报道指/文章称/消息人士」合计 **最多 1 次**，且 **不能出现在 cover 页**。
- 禁止通稿腔词汇：拟、交表、口径、交叉验证、被写作、隐含地、措辞、援引、信源。

【标题 · SEO + 口语】
- `keyword`：可搜索热点词（OpenAI、Gemini、Sora 等）。
- `title` 与 cover 的 `headline` 相同或极接近，**8–14 字**，含 keyword，**大声读一遍不卡壳**。
- cover 的 `subtitle`：**10–16 字**，写悬念或利益点；**禁止**堆媒体名（如「华尔街日报首发·CNBC交叉验证」）。

【分工】你写全部文案；Coze 原样上屏/配音。PPT = narration 的可视化概要，禁止两套说法。

【写作顺序 · 每页】
1. 先写 narration（cover 40–50 字，先钩子后事实；第 2–4 页 55–75 字；outro 45–55 字）
2. 每页 narration 至少 1 句口语（「说白了」「换句话说」「这就尴尬了」等）
3. 全片 1–2 处可轻微调侃或比喻，不人身攻击
4. 再写 headline（口语短标题，不是通讯社标题）
5. 再写 bullets（拆分 narration 已有信息，bullet 标题也要口语）

【5 页结构 · 每页 layout 不同】

| 页 | layout | 画面形态 | bullets |
|----|--------|----------|---------|
| 1 | cover | **封面超大标题** headline + subtitle，像短视频封面 | 0 条 |
| 2 | insight | 中等标题 + 2 条要点 | 2 条 |
| 3 | data | 中央超大 stat + 短说明 | 1–2 条 |
| 4 | story | 2–3 条时间线要点 | 2–3 条 |
| 5 | outro | 一句 takeaway + 关键词标签 | 0–2 条 |

【封面 cover · 最重要】
- headline = 根节点 title（口语大标题，不是完整新闻标题）
- subtitle = 悬念/利益点，不是出处说明
- bullets = []
- 禁止 bullet、禁止「头条」小标签、禁止模板字样

【好坏对照 · 封面必须接近「好例」】
坏例 headline: "OpenAI拟机密IPO交表：消息称最快周五"
坏例 subtitle: "华尔街日报首发·CNBC交叉验证信源口径"
坏例 narration: "文章援引消息人士称：OpenAI正筹备向美国监管方机密递交IPO招股书草案…"

好例 headline: "OpenAI要上市了"
好例 subtitle: "8500亿估值，比苹果还猛？"
好例 narration: "重磅：OpenAI 可能要 IPO 了。私募给的价码已经飙到 8500 亿美元——啥概念？ChatGPT 这家公司要是真敲钟，科技圈都得抖三抖。"

【输出】只输出一个 JSON，不要 markdown，不要解释，不要重复两份 JSON。

JSON 结构：
{{
  "title": "OpenAI要上市了",
  "keyword": "OpenAI",
  "source": {{ "title": "...", "url": "https://...", "site": "CNBC" }},
  "slides": [
    {{
      "layout": "cover",
      "headline": "OpenAI要上市了",
      "subtitle": "8500亿估值，比苹果还猛？",
      "bullets": [],
      "narration": "重磅：OpenAI 可能要 IPO 了。私募估值飙到 8500 亿美元，ChatGPT 要是真敲钟，科技圈都得抖三抖。",
      "image_prompt": "English, vertical 9:16 cinematic cover, no text"
    }},
    {{
      "layout": "insight",
      "headline": "华尔街两大投行操刀",
      "bullets": [{{"title":"高盛+小摩","desc":"报道说两家投行在帮它准备上市材料"}}, {{"title":"公司还在装淡定","desc":"官方只说在评估各种选项，没给时间表"}}],
      "narration": "…",
      "image_prompt": "…"
    }},
    {{
      "layout": "data",
      "headline": "这公司有多贵？",
      "stat": "8500亿美元",
      "bullets": [{{"title":"钱烧得飞快","desc":"报道提到它融了超多钱，花钱速度也创纪录"}}],
      "narration": "…",
      "image_prompt": "…"
    }},
    {{
      "layout": "story",
      "headline": "上市前还有戏看",
      "bullets": […],
      "narration": "…",
      "image_prompt": "…"
    }},
    {{
      "layout": "outro",
      "headline": "真能上市吗？",
      "bullets": [{{"title":"#OpenAI","desc":""}}],
      "narration": "…",
      "image_prompt": "…"
    }}
  ]
}}

用户输入的检索方向：{topic}（可忽略字面，以检索时间窗内最符合「大众向 AI 资讯」的单话题为准）
"""


def build_research_prompt(
    topic: str,
    *,
    days: int = 2,
    exclude_keywords: list[str] | None = None,
    batch_index: int | None = None,
    batch_total: int | None = None,
) -> str:
    exclude_section = ""
    if exclude_keywords:
        joined = "、".join(exclude_keywords)
        exclude_section = (
            f"\n6. **禁止**选择以下已制作话题（keyword 或同一新闻事件）：{joined}"
        )
    batch_section = ""
    if batch_index is not None and batch_total is not None:
        batch_section = (
            f"\n【批次任务】这是第 {batch_index}/{batch_total} 条视频，"
            "必须与已制作列表中的话题/事件完全不同。"
        )
    return RESEARCH_PROMPT.format(
        days=days,
        exclude_section=exclude_section,
        batch_section=batch_section,
        topic=topic,
    )

STYLE_FIX_PROMPT = """你上一轮输出的 JSON 脚本未通过风格校验。请**仅修正文案**（保持 source 与事实不变），重新输出**完整 JSON**。

校验错误：
{errors}

回顾要求：
- title / cover headline：8–14 字口语标题，含 keyword，读起来不拗口
- cover subtitle：悬念或利益点，禁止堆媒体名
- narration：像给朋友讲新闻，短句，有钩子；禁止通稿腔
- 全片「文章认为/报道指/文章称/消息人士」最多 1 次，cover 页不能有
- 禁止：拟、交表、口径、交叉验证、被写作、隐含地、措辞、援引、信源

只输出 JSON，不要 markdown，不要解释。
"""

# 通稿腔禁用词（出现在 headline / title / narration / bullet 即报错）
_BANNED_PHRASES = (
    "口径",
    "交叉验证",
    "被写作",
    "隐含地",
    "交表",
    "措辞",
    "援引",
    "信源",
)
_FORMAL_ATTRIBUTION = re.compile(r"文章认为|报道指|文章称|文章援引|消息人士")
_COVER_BAD_START = re.compile(r"^(文章|报道|消息|援引|据.{1,6}报道)")

# 5 页固定 layout 顺序
_SLIDE_LAYOUTS = ("cover", "insight", "data", "story", "outro")

# 口语标题长度（中文按字符计）
_TITLE_MIN_LEN = 6
_TITLE_MAX_LEN = 16
_SUBTITLE_MIN_LEN = 8
_SUBTITLE_MAX_LEN = 20
_MAX_FORMAL_ATTRIBUTIONS = 1


def _slide_text_fields(slide: dict) -> list[str]:
    parts = [slide.get("headline") or "", slide.get("subtitle") or "", slide.get("narration") or ""]
    for bullet in slide.get("bullets") or []:
        if isinstance(bullet, dict):
            parts.append(bullet.get("title") or "")
            parts.append(bullet.get("desc") or "")
    return [str(p) for p in parts if p]


def _find_banned_phrases(text: str) -> list[str]:
    return [p for p in _BANNED_PHRASES if p in text]


def _keyword_in_text(keyword: str, text: str) -> bool:
    """keyword 须出现在文本中（忽略空格差异）。"""
    kw = keyword.replace(" ", "").lower()
    normalized = text.replace(" ", "").lower()
    return kw in normalized


def validate_style(data: dict) -> None:
    """口语化 / 标题 / 通稿腔门禁；失败抛 ValueError。"""
    errors: list[str] = []
    keyword = str(data["keyword"]).strip()
    title = str(data["title"]).strip()

    if not (_TITLE_MIN_LEN <= len(title) <= _TITLE_MAX_LEN):
        errors.append(f"title 须 {_TITLE_MIN_LEN}–{_TITLE_MAX_LEN} 字，当前 {len(title)} 字：{title!r}")
    if keyword and not _keyword_in_text(keyword, title):
        errors.append(f"title 须含 keyword「{keyword}」")

    for phrase in _find_banned_phrases(title):
        errors.append(f"title 含禁用词「{phrase}」")

    all_narration = ""
    formal_count = 0
    slides = data["slides"]

    for i, slide in enumerate(slides):
        page = i + 1
        for text in _slide_text_fields(slide):
            for phrase in _find_banned_phrases(text):
                errors.append(f"第 {page} 页含禁用词「{phrase}」：{text[:30]}…")
            formal_count += len(_FORMAL_ATTRIBUTION.findall(text))

        narration = str(slide.get("narration") or "")

        if slide.get("layout") == "cover":
            headline = str(slide.get("headline") or "").strip()
            subtitle = str(slide.get("subtitle") or "").strip()

            if headline != title and headline not in title and title not in headline:
                errors.append(f"cover headline 应与 title 相同或极接近：title={title!r} headline={headline!r}")
            if not (_TITLE_MIN_LEN <= len(headline) <= _TITLE_MAX_LEN):
                errors.append(f"cover headline 须 {_TITLE_MIN_LEN}–{_TITLE_MAX_LEN} 字，当前 {len(headline)} 字")
            if not (_SUBTITLE_MIN_LEN <= len(subtitle) <= _SUBTITLE_MAX_LEN):
                errors.append(f"cover subtitle 须 {_SUBTITLE_MIN_LEN}–{_SUBTITLE_MAX_LEN} 字，当前 {len(subtitle)} 字")
            if re.search(r"[·].*[·]|交叉|验证|首发", subtitle):
                errors.append(f"cover subtitle 勿堆媒体名/验证口径：{subtitle!r}")
            if _COVER_BAD_START.match(narration.strip()):
                errors.append("cover 页 narration 禁止以「文章/报道/消息…」开头，须先钩子后事实")

    if formal_count > _MAX_FORMAL_ATTRIBUTIONS:
        errors.append(
            f"全片「文章认为/报道指/文章称/消息人士」最多 {_MAX_FORMAL_ATTRIBUTIONS} 次，当前 {formal_count} 次"
        )

    if errors:
        raise ValueError("风格校验未通过：\n- " + "\n- ".join(errors))


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


def validate_script(data: dict, *, exclude_keywords: list[str] | None = None) -> dict:
    if not isinstance(data, dict):
        raise ValueError("根节点必须是 object")
    for key in ("title", "keyword", "slides", "source"):
        if key not in data:
            raise ValueError(f"缺少 {key}")
    keyword = str(data["keyword"]).strip()
    if len(keyword) < 2:
        raise ValueError("keyword 太短，需为可搜索的热点词")
    if exclude_keywords:
        kw_norm = keyword.replace(" ", "").lower()
        for ex in exclude_keywords:
            ex_norm = str(ex).replace(" ", "").lower()
            if not ex_norm:
                continue
            if kw_norm == ex_norm or kw_norm in ex_norm or ex_norm in kw_norm:
                raise ValueError(f"keyword「{keyword}」与已制作话题「{ex}」重复")

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
    validate_style(data)
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


def _run_agent(prompt: str, agent_id: str | None, on_tool) -> tuple[str, str, str, str]:
    """返回 (text, status, agent_id, run_id)。"""
    if agent_id:
        run_id = create_run(agent_id, prompt)
    else:
        agent_id, run_id = create_agent(prompt)
    text, status = run_with_stream(agent_id, run_id, on_tool_call=on_tool)
    return text, status, agent_id, run_id


def _parse_and_validate(text: str, *, exclude_keywords: list[str] | None = None) -> dict:
    return validate_script(extract_json(text), exclude_keywords=exclude_keywords)


def run_research(
    topic: str,
    *,
    output: str | Path,
    agent_id: str | None = None,
    days: int = 2,
    exclude_keywords: list[str] | None = None,
    batch_index: int | None = None,
    batch_total: int | None = None,
    logs_dir: Path | None = None,
) -> tuple[dict, str]:
    """调研并保存脚本，返回 (script, agent_id)。"""
    logs_dir = logs_dir or (ROOT / "logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    prompt = build_research_prompt(
        topic,
        days=days,
        exclude_keywords=exclude_keywords,
        batch_index=batch_index,
        batch_total=batch_total,
    )

    def on_tool(payload: dict) -> None:
        name = str(payload.get("name") or payload.get("tool") or "")
        if re.search(r"search|web", name, re.I):
            print("  🔍 联网搜索中…")

    text, status, agent_id, run_id = _run_agent(prompt, agent_id, on_tool)
    print(f"  agent={agent_id} run={run_id} status={status}")

    if status != "FINISHED":
        raise RuntimeError(text or "Agent 未正常结束")

    try:
        script = _parse_and_validate(text, exclude_keywords=exclude_keywords)
    except (ValueError, json.JSONDecodeError) as e:
        print(f"  ⚠️  校验未通过，请 Agent 修正文案… ({e})", file=sys.stderr)
        fix_prompt = STYLE_FIX_PROMPT.format(errors=str(e))
        fix_prompt += f"\n\n上一轮 JSON：\n{text}"
        text, status, agent_id, run_id = _run_agent(fix_prompt, agent_id, on_tool)
        print(f"  agent={agent_id} run={run_id} status={status} (修正轮)")
        if status != "FINISHED":
            raise RuntimeError(text or "Agent 修正轮未正常结束")
        script = _parse_and_validate(text, exclude_keywords=exclude_keywords)

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "topic": topic,
        "days": days,
        "batch_index": batch_index,
        "batch_total": batch_total,
        "exclude_keywords": exclude_keywords or [],
        "agent_id": agent_id,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "script": script,
    }
    out_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    agent_store = logs_dir / "cursor_agent.json"
    agent_store.write_text(json.dumps({"agent_id": agent_id}, indent=2), encoding="utf-8")
    return script, agent_id


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="Cursor Agent 本地调研 → JSON 脚本")
    parser.add_argument("topic", nargs="?", default=os.environ.get("COZE_WORKFLOW_TOPIC", "今日AI新闻"))
    parser.add_argument("-o", "--output", default=str(ROOT / "logs" / "last_script.json"))
    parser.add_argument("--agent-id", help="复用已有 agent 追问")
    parser.add_argument("--days", type=int, default=2, help="搜索时间窗（天），默认 2（约 48 小时）")
    parser.add_argument(
        "--exclude",
        help="已制作 keyword，逗号分隔，批量时避免重复",
    )
    parser.add_argument("--batch-index", type=int, help="批次序号（从 1 开始）")
    parser.add_argument("--batch-total", type=int, help="批次总数")
    args = parser.parse_args()

    exclude_keywords = [k.strip() for k in (args.exclude or "").split(",") if k.strip()]
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"[1/2] Cursor 调研: {args.topic}")
    print(f"  model={os.environ.get('CURSOR_MODEL_ID', 'composer-2')}")
    if args.days != 2:
        print(f"  时间窗: 近 {args.days} 天")
    if exclude_keywords:
        print(f"  排除: {', '.join(exclude_keywords)}")

    try:
        script, agent_id = run_research(
            args.topic,
            output=args.output,
            agent_id=args.agent_id,
            days=args.days,
            exclude_keywords=exclude_keywords or None,
            batch_index=args.batch_index,
            batch_total=args.batch_total,
            logs_dir=logs_dir,
        )
    except (ValueError, json.JSONDecodeError, RuntimeError) as e:
        raw_path = logs_dir / "last_research_raw.txt"
        print(f"脚本校验失败: {e}", file=sys.stderr)
        if raw_path.exists():
            print(f"原始回复已保存: {raw_path}", file=sys.stderr)
        return 1

    out_path = Path(args.output)
    print(f"[2/2] 脚本已保存: {out_path}")
    print(f"  关键词={script.get('keyword')} title={script['title']} slides={len(script['slides'])}")
    src = script.get("source") or {}
    if src.get("url"):
        print(f"  参考: {src.get('site', '')} {src.get('title', '')[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
