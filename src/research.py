#!/usr/bin/env python3
"""本地 Cursor Agent 调研：两阶段（选题 + 内容制作），输出口语化口播脚本 JSON。"""

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


SELECT_TOPIC_PROMPT = """你是抖音「{channel}」频道的资深选题编辑。请联网搜索最近 **{days} 天内（必须 48 小时内有真实信息源）** 的 AI 圈热点，**一次性给出 5 个候选话题**，由人类编辑挑选最终拍哪一条。

【频道定位（硬约束）】
- 频道名：{channel}
- 5 条候选必须全部属于这个垂直，不要今天 AI 资讯明天 AI 做饭。即便用户输入跑题，也要把它拉回这个垂直。
- 受众：刷短视频的普通用户（不是从业者）。

【选题质量标准（按重要性）】
1. **快但不瞎蹭**：48 小时内的真实事件，必须能给出 1 篇权威报道 URL（官方博客、Reuters/TheVerge/Bloomberg/Anthropic/OpenAI 官网、知名媒体）；自媒体推文不算。
2. **强钩子潜力**：能用「冲突 / 悬念 / 利益」三选一在前 3 秒抓住人；纯产品发布、纯参数升级直接 pass。
3. **5 张图能讲透**：能拆成 5 个具体画面（手绘示意图）。
4. **共鸣面广**：涉及钱包 / 工作 / 隐私 / 安全 / 学习 / 创作的优先；纯学术/纯企业财报型话题 pass。
5. **5 条之间必须有差异**：不要 5 条都是「OpenAI 又出了什么」；要在主题/角度/情绪上拉开（资讯爆点 + 概念科普 + 工具避坑 + 行业八卦 + 趋势观察混搭）。

【输出（只输出 JSON 数组，长度恰好 5，不要 markdown，不要解释）】
[
  {{
    "topic": "一句话话题描述（10-25 字，普通人秒懂）",
    "keyword": "核心搜索词（2-6 个汉字，后面 title 必须含这几个字）",
    "angle": "钩子角度（一句话，反差/槽点/利益点，写给后面编剧看）",
    "hook_line": "前 3 秒口播钩子（≤30 字，必须能让人停下手指）",
    "source": {{ "title": "权威报道标题", "url": "https://...", "site": "媒体或机构名" }},
    "audience_pain": "谁最想看？戳的是哪个具体痛点？（15-25 字）",
    "visual_outline": "5 张图大致：画面1→画面2→画面3→画面4→画面5",
    "why_it_works": "为啥这条能爆？预估完播/转发哪一项更强（20-40 字）"
  }},
  ... 4 more
]
{exclude_section}{batch_section}

用户输入的检索方向：{topic}（可忽略字面；按时间窗内最适合本垂直的 5 个话题）
"""


CONTENT_PROMPT = """你是「白板手绘 + 段子手」抖音科普编剧。基于下面已选定的话题，输出一个 60-80 秒视频脚本 JSON。
画面风格固定：**笔记本方格纸 + 黑色钢笔手绘示意图 + 中文手写注释**（类似李永乐老师 / 3Blue1Brown 中文版）。
每一段你都要在脑子里**先画出图**，画不出来这段就不能写。

【已选定话题】
- topic: {topic}
- keyword: {keyword}
- angle: {angle}
- hook_line: {hook_line}
- 参考报道: {source_title} ({source_site}) {source_url}

==================================================
【第一原则 · 故事必须连贯（最重要，违反直接判错）】
==================================================
观众是边刷边听的普通人，**听完整段视频脑子里应该能串成一个完整故事**，而不是 5 个零散段子。所以：

★ **抖音 4 指标目标**（写每页时脑子里要带着这 4 个问题）：
   - **完播率**（前 3 秒决定生死）：cover 第一句必须是冲突/悬念/利益三选一，禁止铺垫
   - **收藏**：中间至少 1 页给出"能保存下来用得上"的硬货（清单、对比、量级、可记的金句）
   - **转发**：至少 1 页给出"想转给朋友"的洞察（可共鸣、可吐槽、有立场）
   - **评论**：outro 末尾必须留 1 个**具体可讨论**的问题（不是「点赞关注」「你怎么看」这种空话），观众能直接答出来

A. **Cover 必须给出完整事件骨架**（55-90 字 narration 内必须包含 4 要素）：
   1. 主角是谁（哪家公司/哪个 AI）
   2. 干了一件啥事（用大白话，一句话讲清楚）
   3. 反差/后果是什么（钩子）
   4. 跟观众有啥关系（暗示）
   ❌ 反例：「你以为在摸鱼，其实在教 AI 顶你的岗。Meta 真就离谱——你每敲一下键，都在交学费。」（除了 Meta 一个词，没说发生了什么具体事件）
   ✅ 正例：「Meta 让员工装监控软件录电脑操作教 AI 干活，5 天后就裁了 8000 人。你亲手教的 AI，正排队接你的工位——这事可能也轮到你。」

B. **每页 narration 第一句必须承接上一页**。要么续上一页结尾的悬念，要么用「先说...」「然后呢」「问题来了」「最离谱的是」「最后一个问题」这类衔接句开头。**禁止每页都另起炉灶讲一个孤立的点。**

C. **5 页 = 一条故事线**，对应抖音 60-80 秒黄金结构（不是 5 个独立 layout 拼盘）：
   | 页 | layout  | 时段 | 任务 | bullets 条数 |
   |----|---------|------|------|--------------|
   | 1 | cover    | 前 3 秒 + 立论 | 强钩子 + 抛出完整事件 | 0（必须为空） |
   | 2 | insight  | 转折 1 | 起因：怎么开始的（1 个最反直觉的点） | **2 或 3 条**（硬约束） |
   | 3 | data     | 转折 2 | 高潮：关键数字/量级反差 | **1 或 2 条**（硬约束） |
   | 4 | story    | 转折 3 | 反应：时间线/案例/场景 | **2 或 3 条**（硬约束） |
   | 5 | outro    | 收尾互动 | 落到观众身上 + 具体问题 | 0、1 或 2 条 |

   bullets 是「视频里上屏的要点条」，每条 ≤14 字短句，**数量必须严格遵守上表**，否则判错。

D. **信息密度限制**：每页只允许引入 **1 个新名词** 或 **1 组新数字**。多了就拆，宁可少讲也别堆。
   - 例：第 3 页讲了 8000 人裁员，就不要在同一页再扔出"7000 人转岗""6000 岗冻结"——后者放第 4 页或砍掉。

E. **段子和吐槽不能悬空**：网感词（「真就离谱」「人麻了」「也是醉了」）必须紧跟在它**解释的那句具体事实**后面，不能单独成段。**全片网感词最多 2 处。**

==================================================
【文风 · 要有"知识获得感"，不是新闻流水账】
==================================================

★ **通顺、好懂、像说话** > 一切金句技巧。读出来要顺，不要让观众听完一句还得停下来想 2 秒。
   - 用人话讲事实，用人话讲清楚就够了；**金句、比喻、对比是调味，不是主菜**
   - 全片**只需要 1-2 句"能记住的金句"**集中在最关键的地方（通常 cover 或 outro），其他页**老老实实把事讲清楚就好**，不要每段都硬塞比喻、不要每句都想反差
   - ❌ 反例：「找数学题，从天才手工活，变成了花钱买运气的工业流水线」——这种句子整篇出现 3 次以上，观众会累
   - ✅ 正例：「就是说，这 353 道题里，AI 只解出 9 道，其他全挂了」——朴素、立刻能懂

★ 每页 narration 必须**揉碎讲透**，让普通人也能听懂：
   - 给出一个新事实/新名词后，**下一句必须用最朴素的话解释它**（一句白话即可，不需要文学化）
   - 宁可一页只讲 1 件事但讲深，不要一页堆 3 件事都浮在表面

★ **句子长度**：单句尽量不超过 25 字，能拆就拆成两个短句。长句、套句、定语堆砌一律砍。

★ **主语别省**：观众是耳朵听的，不是眼睛看的，省主语容易听糊。
   - ❌「答错了编译器立刻报错」（谁答错？编译器谁的？）
   - ✅「AI 写错了，编译器立刻报错」

★ **Cover 必须用「旧锚点 + 新事实 + 量级反差」三段式开场**，让观众瞬间 get 这事跨过了某个临界点：
   句式模板（任选其一改写）：
   ① 「你还记得 X 吗？以前 Y，现在 Z」
   ② 「以前 X 是 Y 级别的事，现在 AI 让它变成了 Z」
   ③ 「过去 N 年人类做 X 只能做到 Y，AI 一个 Z 就 W」
   ✅ 标杆例：「你知道软件漏洞吧？就千禧年那种让全世界连夜打补丁的玩意儿——以前找一个能登头条，现在 AI 一个月挖出一万个。」
   ✅ 标杆例：「你被 AI 翻译过英文邮件吧？以前同声传译是金领工种，现在 AI 一个 token 0.0001 美分。翻译这行的天花板，被 AI 拆了。」
   ❌ 反例（流水账）：「Anthropic 拿 AI 扫关键软件，一个月挖出上万个漏洞。」（没有锚点、没有反差、没有时代感）

★ **像跟朋友讲，不是念稿**：
   - 砍掉无信息形容词：「雪片般飞来」「一口气挖出」「悄悄启动」「联手扫描」——这些都是新闻腔，全部删
   - 用动词不用名词化：「找洞」不写成「漏洞发现工作」
   - 一句话不超过 25 字，长了就拆

★ 数字、日期、公司动作必须来自参考报道；从事实里抽出的洞察、对比、比喻可以随便发挥（但不能编造新事实）。

★ 资讯类：全片最多 1 次「据 XX」类客观引述，且不能出现在 cover。

★ 禁用词（出现即判错）：
   - 新闻腔：拟、交表、口径、交叉验证、被写作、隐含地、措辞、援引、信源、文章认为、报道指、联手、揪出、悄悄启动、雪片般、一口气
   - 空话：值得关注、引发热议、再次刷新、令人瞩目
   - 烂大街网感词全片最多 1 处：「人麻了」「也是醉了」「真就」「这就离谱」

==================================================
【画面思维 · 五种构图选一】
==================================================
- 对比图：A vs B（左右分栏，箭头互指）
- 流程图：步骤 1 → 2 → 3
- 类比图：抽象概念画成具体物（神经网络 = 水管/餐厅传菜/快递分拣）
- 数据图：柱状/曲线/百分比 + 卡通小人/箭头标注
- 时间轴：横向箭头 + 节点

`on_image_text` = 这张图上要写的中文短语数组（5-10 条，每条 ≤ 10 字）。生图模型会**把这些字真的画到图里**作为手写注释：
- 必须是图上能"看到"的标签，不要复述 narration
- 至少 1-2 条带吐槽/反差感（如「翻车现场」「人麻了」「AI:???」「事实:并没有」）
- 中文为主，可少量数字 / 「？」「→」「≠」

==================================================
【硬约束】
==================================================
- `keyword`：与已选定的 keyword 一字不差
- `title`：**永远选最简单口语的那版**——能 6-8 字别 12 字，能口语别书面，能动词别名词化。
  - ❌ 反例：「AI解题353题只对9题」（数字堆叠 + 「题」字出现两次）
  - ✅ 正例：「AI解56年数学难题」（简单、有戏剧感、一眼读懂）
  - 字数 6-14；字面必须包含 keyword
- cover 的 `headline` = `title`（一字不差）
- cover 的 `subtitle`：8-18 字悬念或利益点；禁堆媒体名
- `chapter_title`：3-5 字章节短名，让用户一眼知道这段讲啥（例：「事件」「起因」「炸了」「反抗」「关你啥事」「打工人」「翻车」）

==================================================
【Outro（第 5 页）专属规则 · 这页定转发评论数据】
==================================================
Outro **不是总结**，是把 Cover 抛出的事**落到观众自己的日常上**，制造戏剧反差。结构固定：

A. **第一句**：用「同一个 X / 同一套 X」把"事件主角"和"观众日常"放一起对比
   - 例：「数学家用 AI 能啃 56 年的硬骨头……你刷 AI 写作业呢？」
   - 例：「研究员让 AI 一个月挖 1 万个洞……你手机里的 App 呢？」
   - 例：「Anthropic 把 AI 锁进保险柜……你电脑上的 ChatGPT 呢？」

B. **中间一句**：揭示这个反差对观众**意味着什么具体后果**，必须用观众**日常熟悉的场景**（写作业/淘宝/打游戏/刷短视频/手机银行/外卖……），不要抽象名词。

C. **末尾问题**：必须是**二选一立即能答**的具体问题（「你敢 X 吗？」「你信哪个？」「你选 A 还是 B？」「你会拒绝吗？」），观众脑子里 1 秒能蹦答案。
   - ❌ 反例：「你怎么用 AI？」「评论区聊聊」「你怎么看」
   - ❌ 反例：「只给答案不给证明，你抄答案还是等对步骤？」（句子绕，"等对步骤"不知道啥意思）
   - ✅ 正例：「你敢直接抄它的答案吗？」「你的 Updates 还在拖吗？」「下次让你装监控软件，你会拒吗？」

D. **不要重复 Cover 已经说过的事实**，重点在落点和共鸣。

==================================================
【每页字段】
==================================================
- `chapter_title`（3-5 字）
- `concept`（≤ 25 字，本页记得住的一句话）
- `lead_in`（≤ 14 字，本页 narration 第一句的衔接锚点。**cover 可省略；其余 4 页必填**。例：「先说怎么开始的」「然后炸了」「员工没认怂」「最后想想自己」）
- `headline`（上屏中文标题）
- `narration`（口播原文：**cover 55-80 字；2-4 页 100-150 字；outro 55-80 字**。**2-5 页 narration 必须以 lead_in 或其同义改写开头**。字数下限是硬约束：少于下限的内容不够厚，必须再揉一层细节进去）
- `image_prompt`：**英文**，描述这页的手绘构图（"sketch a left-right comparison of X vs Y with arrow ..."），**不用写风格词**（白板/sketch/handwritten 不写，模板会统一加）
- `on_image_text`：**中文**短语数组，5-10 条
- 其他按 layout：cover→subtitle；data→stat；insight/data/story→bullets

==================================================
【输出】
==================================================
只输出一个 JSON 对象，不要 markdown，不要解释。
顶层必须包含：title, keyword, source（与选题相同的 url/title/site）, slides（数组长度恰好 5）。

写完后**自查 6 项**（不通过就重写，不要输出半成品）：
① **把 5 页 narration 大声读一遍**：有没有任何一句读起来拗口、得停下来想？有就改成大白话。
② Cover 第一句是不是钩子（冲突/悬念/利益）？
③ 全片"能记住的金句"总数是不是 **1-2 句**？多于 2 句要砍——每段都金句观众会累。
④ 5 页 narration 是不是一个连贯故事？衔接突兀就重写。
⑤ 是否有任何一页堆了 2 个以上新名词/新数字？有就拆。
⑥ Outro 末尾是不是留了 1 个**具体可答**的问题？
"""


STYLE_FIX_PROMPT = """你上一轮输出的 JSON 脚本未通过校验。请重新输出**完整视频脚本 JSON**（不要 markdown，不要解释）。

校验错误：
{errors}

【必须包含的顶层字段】title, keyword, source, slides（恰好 5 页）
【source 必须使用选题阶段已确定的报道，不要改 url】
{source_hint}

【slides 每页 layout 顺序固定】cover → insight → data → story → outro
每页须有：layout, chapter_title, concept, headline, narration, image_prompt, on_image_text
cover 另有 subtitle；data 另有 stat；insight/data/story 有 bullets。
**第 2-5 页必须有 lead_in（≤14 字衔接锚点），且 narration 第一句要承接 lead_in。**
Cover 的 narration（35-55 字）必须在内部就讲清「主角 + 干了啥 + 后果 + 跟你的关系」四要素。

只输出一个 JSON 对象。
"""


# === 校验配置 ===
_BANNED_PHRASES = (
    "口径", "交叉验证", "被写作", "隐含地", "交表", "措辞", "援引", "信源",
    "联手", "揪出", "悄悄启动", "雪片般", "一口气挖", "引发热议", "再次刷新",
    "令人瞩目", "值得关注",
)
_FORMAL_ATTRIBUTION = re.compile(r"文章认为|报道指|文章称|文章援引|消息人士")
_COVER_BAD_START = re.compile(r"^(文章|报道|消息|援引|据.{1,6}报道)")
_SLIDE_LAYOUTS = ("cover", "insight", "data", "story", "outro")
_TITLE_MIN_LEN = 6
_TITLE_MAX_LEN = 16
_SUBTITLE_MIN_LEN = 6
_SUBTITLE_MAX_LEN = 22
_CHAPTER_TITLE_MIN = 2
_CHAPTER_TITLE_MAX = 6
_MAX_FORMAL_ATTRIBUTIONS = 1

_NARRATION_LIMITS = {
    "cover": (55, 90),
    "insight": (100, 160),
    "data": (100, 160),
    "story": (100, 160),
    "outro": (55, 90),
}


# === Prompt 构建 ===
def build_select_prompt(
    topic: str,
    *,
    days: int = 2,
    exclude_keywords: list[str] | None = None,
    batch_index: int | None = None,
    batch_total: int | None = None,
    channel: str = "AI 热点解读",
) -> str:
    exclude_section = ""
    if exclude_keywords:
        joined = "、".join(exclude_keywords)
        exclude_section = f"\n【硬性排除】不要选与下面 keyword/事件重复的话题：{joined}"
    batch_section = ""
    if batch_index is not None and batch_total is not None:
        batch_section = f"\n【批次】第 {batch_index}/{batch_total} 条，与已选过的话题完全不同。"
    return SELECT_TOPIC_PROMPT.format(
        days=days,
        topic=topic,
        exclude_section=exclude_section,
        batch_section=batch_section,
        channel=channel,
    )


def build_content_prompt(selected: dict) -> str:
    src = selected.get("source") or {}
    return CONTENT_PROMPT.format(
        topic=selected.get("topic", ""),
        keyword=selected.get("keyword", ""),
        angle=selected.get("angle", ""),
        hook_line=selected.get("hook_line", ""),
        source_title=src.get("title", ""),
        source_site=src.get("site", ""),
        source_url=src.get("url", ""),
    )


# === 校验 ===
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
    kw = keyword.replace(" ", "").lower()
    normalized = text.replace(" ", "").lower()
    return kw in normalized


def validate_style(data: dict) -> None:
    errors: list[str] = []
    keyword = str(data["keyword"]).strip()
    title = str(data["title"]).strip()

    if not (_TITLE_MIN_LEN <= len(title) <= _TITLE_MAX_LEN):
        errors.append(f"title 须 {_TITLE_MIN_LEN}-{_TITLE_MAX_LEN} 字，当前 {len(title)}: {title!r}")
    if keyword and not _keyword_in_text(keyword, title):
        errors.append(f"title 须含 keyword「{keyword}」")
    for phrase in _find_banned_phrases(title):
        errors.append(f"title 含禁用词「{phrase}」")

    formal_count = 0
    slides = data["slides"]
    for i, slide in enumerate(slides):
        page = i + 1
        for text in _slide_text_fields(slide):
            for phrase in _find_banned_phrases(text):
                errors.append(f"第 {page} 页含禁用词「{phrase}」")
            formal_count += len(_FORMAL_ATTRIBUTION.findall(text))
        narration = str(slide.get("narration") or "")
        layout = slide.get("layout") or ""
        limit = _NARRATION_LIMITS.get(layout)
        if limit:
            n_len = len(narration.strip())
            lo, hi = limit
            if n_len < lo:
                errors.append(
                    f"第 {page} 页({layout}) narration 太短：{n_len} 字 < {lo}，请揉碎补内容"
                )
            elif n_len > hi:
                errors.append(
                    f"第 {page} 页({layout}) narration 太长：{n_len} 字 > {hi}"
                )
        if slide.get("layout") == "cover":
            headline = str(slide.get("headline") or "").strip()
            subtitle = str(slide.get("subtitle") or "").strip()
            if headline != title and headline not in title and title not in headline:
                errors.append(f"cover headline 应与 title 一致：title={title!r} headline={headline!r}")
            if not (_TITLE_MIN_LEN <= len(headline) <= _TITLE_MAX_LEN):
                errors.append(f"cover headline 须 {_TITLE_MIN_LEN}-{_TITLE_MAX_LEN} 字，当前 {len(headline)}")
            if not (_SUBTITLE_MIN_LEN <= len(subtitle) <= _SUBTITLE_MAX_LEN):
                errors.append(f"cover subtitle 须 {_SUBTITLE_MIN_LEN}-{_SUBTITLE_MAX_LEN} 字，当前 {len(subtitle)}")
            if _COVER_BAD_START.match(narration.strip()):
                errors.append("cover 页 narration 禁止以「文章/报道/消息」开头")

    if formal_count > _MAX_FORMAL_ATTRIBUTIONS:
        errors.append(f"全片客观引述最多 {_MAX_FORMAL_ATTRIBUTIONS} 次，当前 {formal_count} 次")

    if errors:
        raise ValueError("风格校验未通过：\n- " + "\n- ".join(errors))


# === 工具 ===
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


def _unwrap_script(obj: dict) -> dict:
    """Agent 有时把脚本包在 script 字段里。"""
    if "slides" in obj:
        return obj
    inner = obj.get("script")
    if isinstance(inner, dict) and "slides" in inner:
        return inner
    return obj


def extract_json(text: str, *, require_slides: bool = False) -> dict:
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
        # 找下一个 { 或 [
        next_brace = text.find("{", idx)
        next_bracket = text.find("[", idx)
        if next_brace < 0 and next_bracket < 0:
            break
        if next_brace < 0:
            start = next_bracket
        elif next_bracket < 0:
            start = next_brace
        else:
            start = min(next_brace, next_bracket)
        try:
            obj, end = decoder.raw_decode(text, start)
            if isinstance(obj, dict):
                candidates.append(_unwrap_script(obj))
            elif isinstance(obj, list):
                candidates.append({"_array": obj})
            idx = end
        except json.JSONDecodeError:
            idx = start + 1
    if not candidates:
        raise ValueError("无法从 Agent 回复中解析 JSON")

    if require_slides:
        for obj in candidates:
            if isinstance(obj.get("slides"), list) and len(obj["slides"]) >= 1:
                return obj
        raise ValueError("回复中未找到含 slides 的完整脚本 JSON")

    for obj in candidates:
        if "slides" in obj:
            return obj
    for obj in candidates:
        if "topic" in obj and "keyword" in obj and "hook_line" in obj:
            return obj  # 选题 JSON
    return candidates[0]


def extract_topic_candidates(text: str) -> list[dict]:
    """从 Agent 回复中找 5 个选题候选（顶层数组，或包了一层的对象）。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if fence:
        text = fence.group(1).strip()
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(text):
        next_brace = text.find("{", idx)
        next_bracket = text.find("[", idx)
        if next_brace < 0 and next_bracket < 0:
            break
        if next_brace < 0:
            start = next_bracket
        elif next_bracket < 0:
            start = next_brace
        else:
            start = min(next_brace, next_bracket)
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            idx = start + 1
            continue
        idx = end
        if isinstance(obj, list) and obj and all(isinstance(x, dict) for x in obj):
            return obj
        if isinstance(obj, dict):
            for key in ("candidates", "topics", "options", "list"):
                v = obj.get(key)
                if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                    return v
    raise ValueError("Agent 未返回候选数组")


def merge_selection_into_script(data: dict, selection: dict) -> dict:
    """校验前合并选题信息，避免 Agent 漏写 source/keyword。"""
    sel_src = selection.get("source") or {}
    src = data.get("source")
    if not isinstance(src, dict):
        src = {}
    merged_src = {
        "title": src.get("title") or sel_src.get("title") or "",
        "url": src.get("url") or sel_src.get("url") or "",
        "site": src.get("site") or sel_src.get("site") or "",
    }
    data["source"] = merged_src
    if not str(data.get("keyword") or "").strip():
        data["keyword"] = str(selection.get("keyword") or "").strip()
    data["selection"] = selection
    return data


def validate_selection(sel: dict) -> dict:
    if not isinstance(sel, dict):
        raise ValueError("选题结果必须是 object")
    for key in ("topic", "keyword", "angle", "hook_line", "source"):
        if not sel.get(key):
            raise ValueError(f"选题缺少字段: {key}")
    keyword = str(sel["keyword"]).strip()
    if not (2 <= len(keyword) <= 8):
        raise ValueError(f"keyword 须 2-8 字: {keyword!r}")
    src = sel.get("source") or {}
    if not src.get("url", "").startswith("http"):
        raise ValueError("source.url 必须是有效链接")
    return sel


def validate_script(data: dict, *, exclude_keywords: list[str] | None = None) -> dict:
    if not isinstance(data, dict):
        raise ValueError("根节点必须是 object")
    for key in ("title", "keyword", "slides", "source"):
        if key not in data:
            raise ValueError(f"缺少 {key}")
    keyword = str(data["keyword"]).strip()
    if len(keyword) < 2:
        raise ValueError("keyword 太短")
    if exclude_keywords:
        kw_norm = keyword.replace(" ", "").lower()
        for ex in exclude_keywords:
            ex_norm = str(ex).replace(" ", "").lower()
            if ex_norm and (kw_norm == ex_norm or kw_norm in ex_norm or ex_norm in kw_norm):
                raise ValueError(f"keyword「{keyword}」与已制作话题「{ex}」重复")

    src = data.get("source") or {}
    if not src.get("url", "").startswith("http"):
        raise ValueError("source.url 必须是有效链接")
    if not src.get("title"):
        raise ValueError("缺少 source.title")

    slides = data["slides"]
    if not isinstance(slides, list) or len(slides) != 5:
        raise ValueError(f"slides 须恰好 5 页，当前 {len(slides) if isinstance(slides, list) else '非数组'}")
    for i, slide in enumerate(slides):
        expected = _SLIDE_LAYOUTS[i]
        layout = slide.get("layout") or expected
        if layout != expected:
            raise ValueError(f"第 {i+1} 页 layout 应为 {expected}，当前 {layout}")
        slide["layout"] = layout
        if not slide.get("headline") or not slide.get("narration") or not slide.get("image_prompt"):
            raise ValueError(f"第 {i+1} 页缺少 headline/narration/image_prompt")
        chapter = str(slide.get("chapter_title") or "").strip()
        if not (_CHAPTER_TITLE_MIN <= len(chapter) <= _CHAPTER_TITLE_MAX):
            raise ValueError(f"第 {i+1} 页 chapter_title 须 {_CHAPTER_TITLE_MIN}-{_CHAPTER_TITLE_MAX} 字: {chapter!r}")
        if not str(slide.get("concept") or "").strip():
            raise ValueError(f"第 {i+1} 页缺少 concept")
        if layout != "cover":
            lead_in = str(slide.get("lead_in") or "").strip()
            if not lead_in:
                raise ValueError(f"第 {i+1} 页缺少 lead_in（衔接首句，≤14 字）")
            if len(lead_in) > 14:
                raise ValueError(f"第 {i+1} 页 lead_in 须 ≤14 字，当前 {len(lead_in)}: {lead_in!r}")
        on_image_text = slide.get("on_image_text") or []
        if not isinstance(on_image_text, list) or not (3 <= len(on_image_text) <= 12):
            raise ValueError(f"第 {i+1} 页 on_image_text 须 3-12 条")
        for j, item in enumerate(on_image_text):
            if not isinstance(item, str) or len(item) > 16 or not item.strip():
                raise ValueError(f"第 {i+1} 页 on_image_text[{j}] 须 1-16 字非空: {item!r}")
        bullets = slide.get("bullets") or []
        _validate_layout_fields(i, layout, slide, bullets)
    validate_style(data)
    return data


def _validate_layout_fields(index: int, layout: str, slide: dict, bullets: list) -> None:
    if layout == "cover":
        if bullets:
            raise ValueError("cover 页 bullets 必须为空")
        if not slide.get("subtitle"):
            raise ValueError("cover 页须含 subtitle")
    elif layout == "insight":
        if not (2 <= len(bullets) <= 3):
            raise ValueError("insight 页 bullets 须 2-3 条")
    elif layout == "data":
        if not slide.get("stat"):
            raise ValueError("data 页须含 stat")
        if not (1 <= len(bullets) <= 2):
            raise ValueError("data 页 bullets 须 1-2 条")
    elif layout == "story":
        if not (2 <= len(bullets) <= 3):
            raise ValueError("story 页 bullets 须 2-3 条")
    elif layout == "outro":
        if len(bullets) > 2:
            raise ValueError("outro 页 bullets 最多 2 条")


# === Agent 调用 ===
def _on_search(payload: dict) -> None:
    name = str(payload.get("name") or payload.get("tool") or "")
    if re.search(r"search|web", name, re.I):
        print("  🔍 联网搜索中…")


def _run_agent(prompt: str, agent_id: str | None) -> tuple[str, str, str, str]:
    if agent_id:
        run_id = create_run(agent_id, prompt)
    else:
        agent_id, run_id = create_agent(prompt)
    text, status = run_with_stream(agent_id, run_id, on_tool_call=_on_search)
    return text, status, agent_id, run_id


def select_topic_candidates(
    topic: str,
    *,
    days: int = 2,
    agent_id: str | None = None,
    exclude_keywords: list[str] | None = None,
    batch_index: int | None = None,
    batch_total: int | None = None,
    channel: str = "AI 热点解读",
) -> tuple[list[dict], str]:
    """阶段一：拿 5 个选题候选。"""
    prompt = build_select_prompt(
        topic, days=days, exclude_keywords=exclude_keywords,
        batch_index=batch_index, batch_total=batch_total,
        channel=channel,
    )
    text, status, agent_id, run_id = _run_agent(prompt, agent_id)
    print(f"  agent={agent_id} run={run_id} status={status}")
    if status != "FINISHED":
        raise RuntimeError(text or "选题 Agent 未正常结束")
    candidates = extract_topic_candidates(text)
    valid = [c for c in candidates if _candidate_looks_ok(c)]
    if not valid:
        raise RuntimeError("Agent 返回的候选均不合规")
    return valid[:5], agent_id


def _candidate_looks_ok(c: dict) -> bool:
    if not isinstance(c, dict):
        return False
    for key in ("topic", "keyword", "angle", "hook_line", "source"):
        if not c.get(key):
            return False
    src = c.get("source") or {}
    return bool(str(src.get("url") or "").startswith("http"))


def pick_candidate(candidates: list[dict], *, auto: bool = False) -> dict:
    """交互式让用户从候选里选一个；auto=True 时直接选第 1 个。"""
    print()
    print("=" * 64)
    print(f"  候选选题（{len(candidates)} 条）— 请挑一条")
    print("=" * 64)
    for i, c in enumerate(candidates, 1):
        src = c.get("source") or {}
        print(f"\n[{i}] {c.get('topic')}")
        print(f"    keyword : {c.get('keyword')}")
        print(f"    钩子    : {c.get('hook_line')}")
        print(f"    角度    : {c.get('angle')}")
        if c.get("audience_pain"):
            print(f"    痛点    : {c.get('audience_pain')}")
        if c.get("visual_outline"):
            print(f"    画面线  : {c.get('visual_outline')}")
        if c.get("why_it_works"):
            print(f"    为啥爆  : {c.get('why_it_works')}")
        print(f"    来源    : {src.get('site', '')} — {src.get('title', '')[:60]}")
        print(f"    URL     : {src.get('url', '')}")
    print()
    if auto:
        print("[auto] 自动选 [1]")
        return validate_selection(candidates[0])
    while True:
        raw = input(f"请输入 1-{len(candidates)}（回车=1）: ").strip()
        if not raw:
            raw = "1"
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(candidates):
                try:
                    return validate_selection(candidates[idx - 1])
                except ValueError as e:
                    print(f"  ✗ 该选项不合规: {e}，请换一个")
                    continue
        print(f"  ✗ 输入无效，请输入 1-{len(candidates)} 之间的数字")


def _parse_script_response(
    text: str,
    selection: dict,
    *,
    exclude_keywords: list[str] | None = None,
) -> dict:
    raw = extract_json(text, require_slides=True)
    data = merge_selection_into_script(raw, selection)
    return validate_script(data, exclude_keywords=exclude_keywords)


def _source_hint(selection: dict) -> str:
    src = selection.get("source") or {}
    return json.dumps(
        {"source": src, "keyword": selection.get("keyword", "")},
        ensure_ascii=False,
    )


def write_content(
    selection: dict,
    *,
    agent_id: str,
    exclude_keywords: list[str] | None = None,
) -> tuple[dict, str]:
    """阶段二：基于选题写脚本。复用 agent_id 保上下文。"""
    prompt = build_content_prompt(selection)
    text, status, agent_id, run_id = _run_agent(prompt, agent_id)
    print(f"  agent={agent_id} run={run_id} status={status}")
    if status != "FINISHED":
        raise RuntimeError(text or "内容 Agent 未正常结束")

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            return _parse_script_response(
                text, selection, exclude_keywords=exclude_keywords
            ), agent_id
        except (ValueError, json.JSONDecodeError) as e:
            last_err = e
            if attempt >= 2:
                break
            print(f"  ⚠️  校验未通过，请 Agent 修正… ({e})", file=sys.stderr)
            fix_prompt = STYLE_FIX_PROMPT.format(
                errors=str(e),
                source_hint=_source_hint(selection),
            )
            if attempt == 0:
                fix_prompt += f"\n\n上一轮输出：\n{text[:12000]}"
            text, status, agent_id, run_id = _run_agent(fix_prompt, agent_id)
            print(f"  agent={agent_id} run={run_id} status={status} (修正轮 {attempt + 1})")
            if status != "FINISHED":
                raise RuntimeError(text or "Agent 修正轮未正常结束")

    raise RuntimeError(f"内容脚本校验失败（已重试）: {last_err}") from last_err


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
    use_selection: bool = False,
    auto_pick: bool = False,
    channel: str = "AI 热点解读",
) -> tuple[dict, str]:
    """两阶段流程：选题 + 写稿。"""
    logs_dir = logs_dir or (ROOT / "logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    selection_path = logs_dir / "last_selection.json"
    saved_sel: dict | None = None
    if use_selection and selection_path.is_file():
        try:
            saved_sel = validate_selection(json.loads(selection_path.read_text(encoding="utf-8")))
        except (ValueError, json.JSONDecodeError):
            saved_sel = None
    # 若用户传了与已保存选题不相关的新 topic，自动忽略复用
    if saved_sel and topic:
        kw = str(saved_sel.get("keyword", "")).lower()
        sel_topic = str(saved_sel.get("topic", "")).lower()
        t = topic.replace(" ", "").lower()
        if t and (t not in kw and kw not in t and t not in sel_topic):
            print(f"[1a] 已忽略保存的选题（topic={topic} 与上次「{saved_sel.get('topic')}」不同）")
            saved_sel = None

    if saved_sel:
        selection = saved_sel
        if not agent_id and (logs_dir / "cursor_agent.json").is_file():
            try:
                agent_id = json.loads((logs_dir / "cursor_agent.json").read_text())["agent_id"]
            except (json.JSONDecodeError, KeyError):
                agent_id = None
        if not agent_id:
            agent_id, _ = create_agent(build_content_prompt(selection))
        print("[1a] 跳过选题，使用已保存选题")
    else:
        print(f"[1a] 选题（频道={channel}，近 {days} 天热点；先出 5 候选再人工挑）…")
        candidates, agent_id = select_topic_candidates(
            topic, days=days, agent_id=agent_id,
            exclude_keywords=exclude_keywords,
            batch_index=batch_index, batch_total=batch_total,
            channel=channel,
        )
        (logs_dir / "last_candidates.json").write_text(
            json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        selection = pick_candidate(candidates, auto=auto_pick)
    print(f"  ✓ 选定: {selection['topic']} (keyword={selection['keyword']})")
    print(f"    钩子: {selection['hook_line']}")
    src = selection.get("source") or {}
    if src.get("url"):
        print(f"    参考: {src.get('site', '')} {src.get('title', '')[:50]}")
    (logs_dir / "last_selection.json").write_text(
        json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print("[1b] 内容制作（口播 + 画面 + 注释）…")
    script, agent_id = write_content(
        selection, agent_id=agent_id, exclude_keywords=exclude_keywords,
    )

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "topic": topic,
        "days": days,
        "batch_index": batch_index,
        "batch_total": batch_total,
        "exclude_keywords": exclude_keywords or [],
        "agent_id": agent_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "selection": selection,
        "script": script,
    }
    out_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (logs_dir / "cursor_agent.json").write_text(
        json.dumps({"agent_id": agent_id}, indent=2), encoding="utf-8"
    )
    return script, agent_id


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="两阶段调研：选题 + 内容制作")
    parser.add_argument("topic", nargs="?", default=os.environ.get("AIVIDEO_TOPIC", "今日AI热点"))
    parser.add_argument("-o", "--output", default=str(ROOT / "logs" / "last_script.json"))
    parser.add_argument("--agent-id")
    parser.add_argument("--days", type=int, default=2, help="选题时间窗（天），默认 2（48h）")
    parser.add_argument("--exclude", help="已制作 keyword，逗号分隔")
    parser.add_argument("--batch-index", type=int)
    parser.add_argument("--batch-total", type=int)
    parser.add_argument(
        "--use-selection",
        action="store_true",
        help="跳过选题，使用 logs/last_selection.json（选题已成功、仅重跑写稿时）",
    )
    parser.add_argument(
        "--auto-pick", action="store_true",
        help="不交互，直接用 5 候选中的第 1 条（批量跑/CI 用）",
    )
    parser.add_argument(
        "--channel", default=os.environ.get("AIVIDEO_CHANNEL", "AI 热点解读"),
        help="频道定位（垂直标签），默认环境变量 AIVIDEO_CHANNEL 或「AI 热点解读」",
    )
    args = parser.parse_args()

    exclude_keywords = [k.strip() for k in (args.exclude or "").split(",") if k.strip()]
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    print(f"[research] 检索方向: {args.topic}（频道={args.channel}，近 {args.days} 天，模型={os.environ.get('CURSOR_MODEL_ID', 'composer-2.5')}）")
    if exclude_keywords:
        print(f"  排除: {', '.join(exclude_keywords)}")

    try:
        script, _ = run_research(
            args.topic,
            output=args.output,
            agent_id=args.agent_id,
            days=args.days,
            exclude_keywords=exclude_keywords or None,
            batch_index=args.batch_index,
            batch_total=args.batch_total,
            logs_dir=logs_dir,
            use_selection=args.use_selection,
            auto_pick=args.auto_pick,
            channel=args.channel,
        )
    except (ValueError, json.JSONDecodeError, RuntimeError) as e:
        print(f"调研失败: {e}", file=sys.stderr)
        return 1

    print(f"[done] 脚本: {args.output}")
    print(f"  关键词={script.get('keyword')} title={script['title']} slides={len(script['slides'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
