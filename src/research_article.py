#!/usr/bin/env python3
"""文章驱动的调研流水线：搜索 AI 圈热点英文长文 → 选 1 篇 → 按文章自身节奏改编为 3-10 页中文短视频脚本。

与 research.py 的关键差异：
- 阶段一：不指定关键词，搜索"自带观点 + 背景事件 + 完整叙事"的英文长文。
- 阶段三：完全按文章原本结构拆页（3-10 页可变），不强加 5 页模板与"关你啥事"结尾。
- 输出 JSON 的 schema 仍与 enrich_images.py / video_compose.py 兼容。
"""

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
from research import (
    extract_json,
    extract_topic_candidates,
    load_env,
    _run_agent,  # 复用底层 agent 调用与 search 提示
)


# ============================================================
# 阶段一：找文章
# ============================================================
FIND_ARTICLE_PROMPT = """你是 AI 内容编辑，给抖音「{channel}」频道选材。请联网搜索过去 **{days} 天内** 的 AI 领域**英文长文**，**一次性给出 5 篇候选**，由人类编辑挑选最终改编哪一篇。

【候选必须满足】
1. **真实存在的英文文章**，能给出可访问 URL。优先：
   - 高质量 newsletter / 个人博客（Stratechery、Simon Willison、Latent Space、Interconnects、One Useful Thing、Every、AI Snake Oil 等）
   - 一线媒体长稿（NYT、The Verge、Wired、The Atlantic、Bloomberg、FT、Economist、MIT Tech Review、IEEE Spectrum）
   - 实验室/公司官方深度博客（OpenAI、Anthropic、DeepMind、Meta AI、Google Research）
   - HN / Reddit r/MachineLearning 当周热门讨论指向的原文
   - 学术 paper 的优秀解读（不直接选纯 paper）
2. **自带完整叙事**：文章本身有起因—发展—转折—结论；有作者明确观点或一手事实。**纯产品发布稿、纯参数列表、纯榜单 PR 一律 pass。**
3. **热度高**：HN 高分 / Twitter 高转 / 行业讨论度大；不要冷门小博客。
4. **能改编成 3-10 页短视频**：信息密度够、有画面感、有数字或具体场景。
5. **5 篇之间必须有差异**：不要全是 OpenAI 新闻，也不要全是技术评论；事件 / 观点 / 现象 / 历史回顾 / 行业分析混搭。

【重要】不需要预设关键词，不要为了"垂直定位"硬拉回某个方向；只看文章本身够不够好。

【输出（只输出 JSON 数组，长度恰好 5，不要 markdown，不要解释）】
[
  {{
    "title": "原文标题（保留英文原文）",
    "url": "https://...",
    "site": "媒体或作者名（如 Stratechery / Simon Willison / The Verge）",
    "author": "作者名（若有）",
    "published_at": "YYYY-MM-DD（尽量精确）",
    "language": "en",
    "summary_en": "原文 2-3 句英文摘要（≤ 80 词，体现核心论点）",
    "summary_zh": "中文一句话概括（25-50 字，普通人秒懂）",
    "thesis": "作者的核心观点 / 文章想说服你相信什么（一句话）",
    "key_facts": ["原文里最硬的 3-6 个事实/数字/场景，每条 ≤25 字"],
    "narrative_arc": "文章自身的叙事节奏（如：以 X 事件开头 → 拆解 Y → 给出 Z 数据 → 反转到 W → 结论）",
    "why_hot": "为什么这周值得讲（HN xx 分 / 转发量 / 行业反应等，一句话）",
    "estimated_pages": 5,
    "audience_pain": "中文受众里谁会想看？（一句话）"
  }},
  ... 4 more
]
{exclude_section}

频道方向参考（软约束，不强行拉齐）：{channel}
"""


def build_find_article_prompt(
    *,
    days: int = 7,
    channel: str = "AI 深度",
    exclude_urls: list[str] | None = None,
) -> str:
    exclude_section = ""
    if exclude_urls:
        joined = "\n  - ".join(exclude_urls)
        exclude_section = f"\n【硬性排除】不要再选这些 URL：\n  - {joined}"
    return FIND_ARTICLE_PROMPT.format(
        days=days, channel=channel, exclude_section=exclude_section
    )


def _article_looks_ok(c: dict) -> bool:
    if not isinstance(c, dict):
        return False
    if not str(c.get("url") or "").startswith("http"):
        return False
    for key in ("title", "site", "summary_zh", "thesis", "key_facts"):
        if not c.get(key):
            return False
    facts = c.get("key_facts") or []
    return isinstance(facts, list) and len(facts) >= 2


def find_articles(
    *,
    days: int = 7,
    channel: str = "AI 深度",
    exclude_urls: list[str] | None = None,
    agent_id: str | None = None,
) -> tuple[list[dict], str]:
    prompt = build_find_article_prompt(
        days=days, channel=channel, exclude_urls=exclude_urls
    )
    text, status, agent_id, run_id = _run_agent(prompt, agent_id)
    print(f"  agent={agent_id} run={run_id} status={status}")
    if status != "FINISHED":
        raise RuntimeError(text or "找文章 Agent 未正常结束")
    candidates = extract_topic_candidates(text)
    valid = [c for c in candidates if _article_looks_ok(c)]
    if not valid:
        raise RuntimeError("Agent 返回的候选文章均不合规")
    return valid[:5], agent_id


def pick_article(candidates: list[dict], *, auto: bool = False) -> dict:
    print()
    print("=" * 72)
    print(f"  候选英文长文（{len(candidates)} 篇）— 请挑一篇改编")
    print("=" * 72)
    for i, c in enumerate(candidates, 1):
        print(f"\n[{i}] {c.get('title')}")
        print(f"    站点    : {c.get('site')}  作者: {c.get('author') or '-'}  日期: {c.get('published_at') or '-'}")
        print(f"    一句话  : {c.get('summary_zh')}")
        print(f"    论点    : {c.get('thesis')}")
        print(f"    叙事    : {c.get('narrative_arc')}")
        print(f"    建议页数: {c.get('estimated_pages')}")
        print(f"    为啥热  : {c.get('why_hot')}")
        facts = c.get("key_facts") or []
        if facts:
            print(f"    硬事实  :")
            for f in facts[:6]:
                print(f"        · {f}")
        print(f"    URL     : {c.get('url')}")
    print()
    if auto:
        print("[auto] 自动选 [1]")
        return candidates[0]
    while True:
        raw = input(f"请输入 1-{len(candidates)}（回车=1）: ").strip() or "1"
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(candidates):
                return candidates[idx - 1]
        print(f"  ✗ 无效，请输入 1-{len(candidates)}")


# ============================================================
# 阶段二：基于文章改编脚本
# ============================================================
ADAPT_SCRIPT_PROMPT = """你是抖音「白板手绘 + 段子手」科普编剧。下面是一篇英文长文，请把它**忠实地改编**成一个中文短视频脚本（3-10 页可变）。

【已选定文章】
- 标题: {title}
- 站点: {site}  作者: {author}  日期: {published_at}
- URL: {url}
- 中文一句话: {summary_zh}
- 作者核心观点: {thesis}
- 文章自身叙事节奏: {narrative_arc}
- 硬事实清单:
{key_facts_block}

==================================================
【第一原则 · 忠于原文，不要硬塞模板】
==================================================
★ **以文章自身的逻辑结构来拆页**，不是硬套 cover/insight/data/story/outro 五段式。
   - 如果原文是「事件 → 解读 → 数据 → 反思」就拆 4 页。
   - 如果原文是「钩子 → 三个论据 → 反例 → 结论 → 余波」就拆 6 页。
   - 如果原文足够丰富信息密度高，可以拆到 8-10 页。**最少 3 页，最多 10 页。**
   - 每一页都必须对应原文里**真实存在的一段内容**（一个段落、一个论点、一个数据、一个场景）。**不要凭空补段，不要为了凑页数兑水。**

★ **观点 / 事实 / 数字必须来自原文**：你的工作是翻译 + 转写为口语 + 选画面，不是另写一篇。
   - 允许你在解释术语时打小比方（一句话以内）。
   - 允许你重组顺序、合并重复段落、砍掉不重要的内容。
   - **不允许**虚构原文里没有的事实、数字、引语；**不允许**硬塞作者没说过的观点。

★ **不要套"关你啥事"模板**。结尾听文章自己的——
   - 如果原文以反问 / 警示 / 留白结尾，你也照做。
   - 如果原文以一句金句结尾，把那句翻成中文上屏。
   - 如果原文有明确建议，老老实实给建议，不要强行扯到「打工人」。
   - 末尾**可以**有评论引子，但**只有在自然的时候**才加；不要刻意。

==================================================
【页数与节奏】
==================================================
- **第 1 页（cover）**：钩子 + 用大白话讲清这篇文章在讲什么事 / 什么观点。必须让没读过原文的人 3 秒内 get 到主题。
- **中间页（body）**：每页只讲一个论点 / 一个数字组 / 一个场景。**信息密度限制：每页只允许 1 个新名词或 1 组新数字。**
- **最后一页**：跟着原文走，不强行套模板。可以是结论、可以是反问、可以是悬念。

【每页 narration 字数】
- cover：50-90 字
- 中间页：80-150 字（信息密度高的可以到 180 字）
- 末页：50-100 字
- 单句 ≤ 25 字，能拆就拆。主语别省。

【文风】
- 像跟朋友讲，不是念新闻稿。砍掉新闻腔（"援引""信源""文章认为""据 XX 报道"等）。
- 全片最多 1 处「据原文」类客观引述，且不在 cover。
- 每页第一句承接上一页（除 cover 外，必须有 lead_in 衔接锚点）。
- 网感词全片最多 2 处。能不用就不用。
- 每个新名词出现后，**下一句必须用一句白话解释**。

==================================================
【画面】
==================================================
风格固定：**笔记本方格纸 + 黑色钢笔手绘示意图 + 中文手写注释**（类似李永乐 / 3Blue1Brown 中文版）。
五种构图任选：对比图 / 流程图 / 类比图 / 数据图 / 时间轴。

`image_prompt`：**英文**，描述这页的手绘构图（不写风格词，模板会统一加）。
`on_image_text`：**中文**短语数组，3-10 条，每条 ≤ 12 字。是图上能看到的标签，不是复述 narration。

==================================================
【每页字段】
==================================================
- `layout`：第 1 页填 "cover"，其余全部填 "body"
- `chapter_title`：3-5 字章节短名
- `concept`：≤25 字，本页一句话
- `lead_in`：≤14 字衔接锚点（cover 可省，其余必填）
- `headline`：上屏中文标题（6-14 字）
- `narration`：口播原文（按上面字数要求）
- `image_prompt`：英文画面描述
- `on_image_text`：3-10 条中文标签数组
- 仅 cover 额外有 `subtitle`（8-22 字，悬念或核心观点）

==================================================
【顶层字段】
==================================================
- `title`：6-14 字中文标题（视频标题，不必跟原文标题一字不差，但必须传达原文核心）
- `keyword`：2-8 字中文关键词（从原文里抽一个最贴切的）
- `source`：{{ "title": 原文英文标题, "url": 原文 URL, "site": 站点 }}
- `slides`：数组，**长度 3-10**

==================================================
【输出】
==================================================
只输出一个 JSON 对象，不要 markdown，不要解释。

写完后**自查**：
① 每一页是否都对应原文里真实存在的内容？
② 有没有为了凑页数兑水的段落？有就删。
③ 有没有虚构原文里没有的数字 / 引语 / 观点？有就改。
④ Cover 第一句是不是钩子？
⑤ 末页是不是顺着原文自己的结尾，而不是硬套"关你啥事"？
"""


def build_adapt_prompt(article: dict) -> str:
    facts = article.get("key_facts") or []
    facts_block = "\n".join(f"  · {f}" for f in facts) or "  · (无)"
    return ADAPT_SCRIPT_PROMPT.format(
        title=article.get("title", ""),
        site=article.get("site", ""),
        author=article.get("author") or "-",
        published_at=article.get("published_at") or "-",
        url=article.get("url", ""),
        summary_zh=article.get("summary_zh", ""),
        thesis=article.get("thesis", ""),
        narrative_arc=article.get("narrative_arc", ""),
        key_facts_block=facts_block,
    )


# ============================================================
# 校验：宽松版（页数 3-10、layout 只分 cover/body）
# ============================================================
_BANNED_PHRASES = (
    "口径", "交叉验证", "被写作", "隐含地", "交表", "措辞", "援引", "信源",
    "联手", "揪出", "悄悄启动", "雪片般", "一口气挖", "引发热议", "再次刷新",
    "令人瞩目", "值得关注",
)
_FORMAL_ATTRIBUTION = re.compile(r"文章认为|报道指|文章称|文章援引|消息人士")
_COVER_BAD_START = re.compile(r"^(文章|报道|消息|援引|据.{1,6}报道)")


def _slide_texts(slide: dict) -> list[str]:
    return [
        str(slide.get("headline") or ""),
        str(slide.get("subtitle") or ""),
        str(slide.get("narration") or ""),
    ]


def validate_article_script(data: dict, article: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("根节点必须是 object")
    for key in ("title", "keyword", "slides", "source"):
        if key not in data:
            raise ValueError(f"缺少 {key}")

    title = str(data["title"]).strip()
    if not (4 <= len(title) <= 18):
        raise ValueError(f"title 须 4-18 字，当前 {len(title)}: {title!r}")

    src = data.get("source") or {}
    if not src.get("url", "").startswith("http"):
        raise ValueError("source.url 必须是有效链接")

    slides = data["slides"]
    if not isinstance(slides, list) or not (3 <= len(slides) <= 10):
        raise ValueError(f"slides 数量须 3-10，当前 {len(slides) if isinstance(slides, list) else '非数组'}")

    formal_count = 0
    for i, slide in enumerate(slides):
        page = i + 1
        layout = slide.get("layout") or ("cover" if i == 0 else "body")
        slide["layout"] = layout
        if i == 0 and layout != "cover":
            raise ValueError("第 1 页 layout 必须为 cover")
        if i > 0 and layout == "cover":
            raise ValueError(f"第 {page} 页不应为 cover")

        for key in ("headline", "narration", "image_prompt", "chapter_title", "concept"):
            if not str(slide.get(key) or "").strip():
                raise ValueError(f"第 {page} 页缺少 {key}")

        ch = str(slide["chapter_title"]).strip()
        if not (2 <= len(ch) <= 6):
            raise ValueError(f"第 {page} 页 chapter_title 须 2-6 字: {ch!r}")

        if layout == "cover":
            if not str(slide.get("subtitle") or "").strip():
                raise ValueError("cover 页缺少 subtitle")
            sub = str(slide["subtitle"]).strip()
            if not (6 <= len(sub) <= 24):
                raise ValueError(f"cover subtitle 须 6-24 字，当前 {len(sub)}")
            if _COVER_BAD_START.match(str(slide["narration"]).strip()):
                raise ValueError("cover narration 禁止以「文章/报道/消息/据...」开头")
        else:
            lead_in = str(slide.get("lead_in") or "").strip()
            if not lead_in:
                raise ValueError(f"第 {page} 页缺少 lead_in（≤14 字衔接锚点）")
            if len(lead_in) > 14:
                raise ValueError(f"第 {page} 页 lead_in ≤14 字，当前 {len(lead_in)}")

        n = str(slide["narration"]).strip()
        nlen = len(n)
        if layout == "cover":
            if not (45 <= nlen <= 95):
                raise ValueError(f"cover narration 须 45-95 字，当前 {nlen}")
        else:
            if not (60 <= nlen <= 200):
                raise ValueError(f"第 {page} 页 narration 须 60-200 字，当前 {nlen}")

        oit = slide.get("on_image_text") or []
        if not isinstance(oit, list) or not (3 <= len(oit) <= 12):
            raise ValueError(f"第 {page} 页 on_image_text 须 3-12 条")
        for j, item in enumerate(oit):
            if not isinstance(item, str) or not item.strip() or len(item) > 16:
                raise ValueError(f"第 {page} 页 on_image_text[{j}] 须 1-16 字非空: {item!r}")

        for txt in _slide_texts(slide):
            for p in _BANNED_PHRASES:
                if p in txt:
                    raise ValueError(f"第 {page} 页含禁用词「{p}」")
            formal_count += len(_FORMAL_ATTRIBUTION.findall(txt))

    if formal_count > 1:
        raise ValueError(f"全片客观引述最多 1 次，当前 {formal_count} 次")

    return data


def merge_article_into_script(data: dict, article: dict) -> dict:
    src = data.get("source")
    if not isinstance(src, dict):
        src = {}
    data["source"] = {
        "title": src.get("title") or article.get("title") or "",
        "url": src.get("url") or article.get("url") or "",
        "site": src.get("site") or article.get("site") or "",
    }
    if not str(data.get("keyword") or "").strip():
        data["keyword"] = (article.get("summary_zh") or "")[:6] or "AI"
    data["article"] = article
    return data


ADAPT_FIX_PROMPT = """你上一轮输出的 JSON 脚本未通过校验。请重新输出**完整脚本 JSON**（不要 markdown，不要解释）。

校验错误：
{errors}

仍按之前要求：
- slides 长度 3-10；第 1 页 layout=cover（含 subtitle），其余 layout=body（含 lead_in）
- 每页有 chapter_title / concept / headline / narration / image_prompt / on_image_text
- 必须忠实于已选定文章原文（URL: {url}），不虚构事实
"""


def adapt_article_to_script(
    article: dict,
    *,
    agent_id: str,
) -> tuple[dict, str]:
    prompt = build_adapt_prompt(article)
    text, status, agent_id, run_id = _run_agent(prompt, agent_id)
    print(f"  agent={agent_id} run={run_id} status={status}")
    if status != "FINISHED":
        raise RuntimeError(text or "改编 Agent 未正常结束")

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            raw = extract_json(text, require_slides=True)
            data = merge_article_into_script(raw, article)
            return validate_article_script(data, article), agent_id
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            if attempt >= 2:
                break
            print(f"  ⚠️  校验未通过，请 Agent 修正… ({e})", file=sys.stderr)
            fix_prompt = ADAPT_FIX_PROMPT.format(errors=str(e), url=article.get("url", ""))
            if attempt == 0:
                fix_prompt += f"\n\n上一轮输出：\n{text[:12000]}"
            text, status, agent_id, run_id = _run_agent(fix_prompt, agent_id)
            print(f"  agent={agent_id} run={run_id} status={status} (修正轮 {attempt + 1})")
            if status != "FINISHED":
                raise RuntimeError(text or "Agent 修正轮未正常结束")
    raise RuntimeError(f"改编脚本校验失败（已重试）: {last_err}") from last_err


# ============================================================
# 主入口
# ============================================================
def run_article_research(
    *,
    output: str | Path,
    days: int = 7,
    channel: str = "AI 深度",
    exclude_urls: list[str] | None = None,
    agent_id: str | None = None,
    logs_dir: Path | None = None,
    use_selection: bool = False,
    auto_pick: bool = False,
) -> tuple[dict, str]:
    logs_dir = logs_dir or (ROOT / "logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    selection_path = logs_dir / "last_article.json"
    saved: dict | None = None
    if use_selection and selection_path.is_file():
        try:
            saved = json.loads(selection_path.read_text(encoding="utf-8"))
            if not _article_looks_ok(saved):
                saved = None
        except json.JSONDecodeError:
            saved = None

    if saved:
        article = saved
        if not agent_id:
            agent_id, _ = create_agent(build_adapt_prompt(article))
        print("[1a] 跳过找文章，复用 logs/last_article.json")
    else:
        print(f"[1a] 搜索过去 {days} 天 AI 圈热点英文长文（5 候选）…")
        candidates, agent_id = find_articles(
            days=days, channel=channel,
            exclude_urls=exclude_urls, agent_id=agent_id,
        )
        (logs_dir / "last_article_candidates.json").write_text(
            json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        article = pick_article(candidates, auto=auto_pick)

    print(f"  ✓ 选定: {article.get('title')}")
    print(f"    站点: {article.get('site')}  日期: {article.get('published_at')}")
    print(f"    URL : {article.get('url')}")
    selection_path.write_text(
        json.dumps(article, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("[1b] 按文章自身节奏改编为 3-10 页中文脚本…")
    script, agent_id = adapt_article_to_script(article, agent_id=agent_id)

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "mode": "article",
        "days": days,
        "channel": channel,
        "agent_id": agent_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "article": article,
        "script": script,
    }
    out_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (logs_dir / "cursor_agent.json").write_text(
        json.dumps({"agent_id": agent_id}, indent=2), encoding="utf-8"
    )
    return script, agent_id


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="文章驱动调研：找英文长文 + 改编中文短视频脚本")
    parser.add_argument("-o", "--output", default=str(ROOT / "logs" / "last_script.json"))
    parser.add_argument("--days", type=int, default=7, help="搜索时间窗（天），默认 7")
    parser.add_argument("--channel", default=os.environ.get("AIVIDEO_CHANNEL", "AI 深度"))
    parser.add_argument("--exclude-urls", help="已制作过的 URL，逗号分隔")
    parser.add_argument("--agent-id")
    parser.add_argument("--use-selection", action="store_true",
                        help="跳过找文章，复用 logs/last_article.json")
    parser.add_argument("--auto-pick", action="store_true",
                        help="不交互，直接选第 1 篇")
    args = parser.parse_args()

    exclude_urls = [u.strip() for u in (args.exclude_urls or "").split(",") if u.strip()]
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"[research_article] 模式=文章驱动 | 频道={args.channel} | 近 {args.days} 天 | "
          f"模型={os.environ.get('CURSOR_MODEL_ID', 'composer-2.5')}")

    try:
        script, _ = run_article_research(
            output=args.output,
            days=args.days,
            channel=args.channel,
            exclude_urls=exclude_urls or None,
            agent_id=args.agent_id,
            logs_dir=logs_dir,
            use_selection=args.use_selection,
            auto_pick=args.auto_pick,
        )
    except (ValueError, json.JSONDecodeError, RuntimeError) as e:
        print(f"调研失败: {e}", file=sys.stderr)
        return 1

    print(f"[done] 脚本: {args.output}")
    print(f"  title={script['title']}  slides={len(script['slides'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
