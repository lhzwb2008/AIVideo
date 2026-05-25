#!/usr/bin/env python3
"""本地 Cursor Agent 调研：每日锁定一个大众向 AI 热点，输出口语化口播脚本 JSON。"""

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

RESEARCH_PROMPT = """你是「概念科普 + AI 资讯」抖音竖屏视频编剧。请联网完成以下任务，输出 60–80 秒视频脚本。
目标观众是刷短视频的普通用户。视频画面是**笔记本方格纸 + 黑色手绘示意图 + 中文手写注释**的科普风（类似「李永乐老师」「3Blue1Brown」中文版的白板讲解），所以你写的每一段，**画面都要能用一张概念图解释清楚**。

【选题 · 必须遵守】
1. 先联网搜索（{days} 天内 AI 资讯热点），结合常青概念，**只选 1 个**话题。
2. 候选两类：
   A. **AI 热点资讯**（产品发布、翻车、涨价、能力突破等）——必须能拆出「为什么/怎么做到/有啥影响」三段可视化解释，不只是新闻陈述
   B. **AI/科技概念科普**（transformer 是什么、为什么模型会幻觉、RLHF 怎么训练、context window 是啥等）——更适合白板风
3. 自评：是否能用 5 张手绘示意图讲清？是否一句话就能让普通人记住？过不了关就换话题。
4. 若选 A 类资讯，需找 **1 篇** 权威报道；事实/数字/日期/公司动作必须来自该文。若选 B 类概念，可参考 1 篇权威解释（OpenAI 博客、Anthropic、维基等）。
{exclude_section}{batch_section}

【画面思维 · 极重要】
你不是在写口播稿，你是在**导演一系列白板手绘图**。每一段 narration 都要对应一张可画出来的图：
- 对比图：A vs B（左右分栏，箭头互指）
- 流程图：步骤 1 → 2 → 3（带箭头）
- 类比图：把抽象概念画成具体物（神经网络画成水管/路由器）
- 数据图：柱状/曲线/百分比+卡通小人
- 时间轴：横向箭头+节点

每段必须输出 `on_image_text`：5–10 条**要画在图上的中文短语**（每条 ≤ 10 字），用于让生图模型把它们当作手写注释画进图里。

【事实与文风】
- 口语化，类比为主，不堆术语；术语出现时下一句必须翻译。
- 资讯类：事实不能改、不能编；全片「文章认为/报道指/消息人士」最多 1 次，cover 不能有。
- 禁用词：拟、交表、口径、交叉验证、被写作、隐含地、措辞、援引、信源。

【标题（硬约束，不满足直接判错）】
- `keyword`：可搜索的热点词或核心概念，**必须是 2–6 个汉字**（例：「幻觉」「Sora」「智能体」），**不允许英文短语**
- `title`：**6–14 字**，**字面必须包含 keyword 这几个字**（例 keyword=「幻觉」→ title 里必须出现"幻觉"两个字），口语化、读起来不拗口
- cover 的 `headline` = `title`（一字不差）
- cover 的 `subtitle`：**8–18 字** 悬念/利益点；禁堆媒体名

【5 页结构 · 每页 layout 固定】

| 页 | layout | 内容定位 | bullets |
|----|--------|----------|---------|
| 1 | cover    | 抛出问题/钩子；headline=核心问题，subtitle=悬念 | 0 条 |
| 2 | insight  | 给出第一个解释维度 | 2–3 条 |
| 3 | data     | 一个关键数字或对比 stat | 1–2 条 |
| 4 | story    | 推演/案例/时间轴 | 2–3 条 |
| 5 | outro    | 一句 takeaway + 标签 | 0–2 条 |

【每页必填字段】
- `chapter_title`：**3–6 字**章节短名（进度条用，例：「问题」「原理」「数字」「案例」「结论」或更具体的「死亡」「跨学科」「为啥幻觉」）
- `concept`：这一段要让观众**记住的一句话**（≤ 25 字）
- `narration`：口播原文（cover 35–55 字；2–4 页 60–90 字；outro 35–55 字）
- `image_prompt`：**英文**，描述这一页要画的手绘示意图布局（"sketch a left-right comparison of X vs Y, with arrow pointing from A to B"），**不用描述风格**（风格由生图模板统一加）
- `on_image_text`：**中文**短语数组，5–10 条，**这些字会被画在图上**作为手写注释。例：["相对论","觉悟","可验证的客观知识","声称超越所有概念的概念","用概念指向超越概念?"]
- `headline`：上屏中文标题（不是字幕，字幕走 narration 自动生成）
- 其他按 layout：cover 加 subtitle；data 加 stat；insight/data/story 加 bullets

【输出】只输出一个 JSON，不要 markdown，不要解释。

示例（话题：「AI 为什么会一本正经胡说八道」）：
{{
  "title": "AI为啥爱瞎编",
  "keyword": "幻觉",
  "source": {{ "title": "Why language models hallucinate", "url": "https://...", "site": "OpenAI" }},
  "slides": [
    {{
      "layout": "cover",
      "chapter_title": "钩子",
      "concept": "AI胡说八道不是bug，是它学的方式",
      "headline": "AI为啥爱瞎编",
      "subtitle": "明明不知道，却答得理直气壮",
      "bullets": [],
      "narration": "你问 ChatGPT 一个它没见过的问题，它居然能编得有鼻子有眼。这不是 bug，是设计。今天 5 张图讲清楚。",
      "image_prompt": "Sketch a chat bubble with a confused human asking a question, and a robot replying with a long fancy answer. Add a small thought-bubble above the robot showing question marks.",
      "on_image_text": ["AI为啥爱瞎编", "用户：拿破仑的火星基地?", "AI：1809年建于...", "事实：根本不存在", "?"]
    }},
    {{
      "layout": "insight",
      "chapter_title": "目标错位",
      "concept": "训练目标是「像人」而不是「正确」",
      "headline": "它学的不是真假",
      "bullets": [{{"title":"训练目标","desc":"预测下一个词，不是判断真假"}}, {{"title":"奖励机制","desc":"答得流畅就给奖励"}}],
      "narration": "...",
      "image_prompt": "Sketch a comparison: left side a human teacher writing 'TRUE / FALSE' on board; right side a robot reading text and predicting next word with arrows.",
      "on_image_text": ["人类老师", "判断真假", "AI模型", "预测下一个词", "≠真假", "→流畅就行"]
    }},
    ...
  ]
}}

用户输入的检索方向：{topic}（可忽略字面，按时间窗内最适合白板讲解的话题为准）
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
_SUBTITLE_MIN_LEN = 6
_SUBTITLE_MAX_LEN = 22
_CHAPTER_TITLE_MIN = 2
_CHAPTER_TITLE_MAX = 8
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
        chapter = str(slide.get("chapter_title") or "").strip()
        if not (_CHAPTER_TITLE_MIN <= len(chapter) <= _CHAPTER_TITLE_MAX):
            raise ValueError(
                f"第 {i + 1} 页 chapter_title 须 {_CHAPTER_TITLE_MIN}–{_CHAPTER_TITLE_MAX} 字，当前 {len(chapter)} 字: {chapter!r}"
            )
        if not str(slide.get("concept") or "").strip():
            raise ValueError(f"第 {i + 1} 页缺少 concept（核心一句话）")
        on_image_text = slide.get("on_image_text") or []
        if not isinstance(on_image_text, list) or not (3 <= len(on_image_text) <= 12):
            raise ValueError(
                f"第 {i + 1} 页 on_image_text 须为 3–12 条字符串数组，当前 {len(on_image_text) if isinstance(on_image_text, list) else '非数组'}"
            )
        for j, item in enumerate(on_image_text):
            if not isinstance(item, str) or len(item) > 16 or not item.strip():
                raise ValueError(f"第 {i + 1} 页 on_image_text[{j}] 应为 1–16 字非空字符串：{item!r}")
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
    parser.add_argument("topic", nargs="?", default=os.environ.get("AIVIDEO_TOPIC", "今日AI新闻"))
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
