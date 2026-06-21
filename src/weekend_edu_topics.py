#!/usr/bin/env python3
"""周末科普教育选题：Opus 根据历史动态选题，不写死话题池。

与工作日新闻槽位分离；Windows 主入口 make-and-publish.ps1 同样走本模块。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from cursor_client import create_agent, create_run, model_id, run_with_stream
from cursor_daily_topics import CURSOR_SLOT_ORDER, china_today
from paths import ROOT
from research import deep_read_article, extract_json, load_env
from text_client import chat_complete, text_model

EDU_CATEGORY_ORDER = ("basic", "quant", "valuation")

EDU_SLOT_LABEL: dict[str, str] = {
    "basic": "财经基础科普",
    "quant": "量化入门科普",
    "valuation": "估值与计算科普",
}

_EDU_DRAFT_PROMPT = """你是「AI财知道」的**财经科普教育撰稿人**（不是新闻编辑、不是收评写手）。

请围绕下面这个**固定科普话题**写一篇面向零基础观众的中文 Markdown 长文（1200–2000 字）：

【科普话题】{title}

写作要求：
1. **只讲概念、原理、公式怎么算、怎么理解**——不要写成当日新闻、不要写具体个股涨跌、不要写「今天市场」
2. 结构建议：
   - 一级标题：用问句或「X是什么/怎么算」形式，与话题一致
   - 一句话结论（60 字内）
   - 先讲「它解决什么问题」（生活化类比 1–2 个）
   - 定义与核心公式/计算步骤（分步写清，举例用虚构的简化数字）
   - 常见误读 2–3 条
   - 和相邻概念的区别（若有）
   - 小结：普通人怎么用这个概念理解财经新闻（不给买卖建议）
3. 可联网查证标准定义、经典公式与教材表述，交叉验证；**禁止编造**搜不到的具体公司股价或日期
4. **严禁**：股票代码、荐股、目标价、买卖建议、仓位建议、保证收益
5. 全文中文 Markdown，直接输出正文，不要提纲-only，不要说「见附件」
"""

EDU_PICK_SYSTEM = """你是「AI财知道」周末财经科普栏目的选题编辑。
观众是完全不懂财经的小白；每条话题应能做成 3–4 分钟竖屏科普短视频。
只输出一个严格 JSON 对象，不要 markdown、不要解释。"""

EDU_PICK_USER = """【今天】{today}
【需要选题数量】{count}
{category_hint}
【栏目参考】
- basic：财经基础（宏观指标、财务术语、市场常识）
- quant：量化入门（因子、回测、程序化、风险指标等）
- valuation：估值与计算（PE/PB/EV/DCF 等怎么理解、怎么算）

【近 {dedup_days} 天已发布/已制作的标题（禁止重复、禁止换皮相似）】
{recent_titles}
{reject_block}
【要求】
1. 每个话题 12–28 字，用「X是什么/怎么算/怎么看」形式，聚焦**一个**概念
2. 不要当日新闻、不要个股、不要荐股
3. count=1 时选 1 个与历史差异最大、最值得做的概念即可
4. count≥3 时尽量覆盖 basic / quant / valuation 三类各至少 1 个
5. theme_cluster 用 edu_ 开头的英文 snake_case，概括概念即可

输出 JSON：
{{
  "topics": [
    {{
      "title": "话题标题",
      "category": "basic|quant|valuation",
      "theme_cluster": "edu_pe_ratio",
      "cold_open": "冷开场一句（≤50字）",
      "angle": "讲解角度",
      "reason": "为什么选它、与历史如何区分"
    }}
  ]
}}
"""


def is_weekend_edu_mode(d: date | None = None) -> bool:
    """是否走周末科普模式（周六日；AIVIDEO_FORCE_WEEKDAY=1 强制工作日）。"""
    if os.environ.get("AIVIDEO_FORCE_WEEKDAY", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    if os.environ.get("AIVIDEO_FORCE_WEEKEND_EDU", "").strip().lower() in ("1", "true", "yes", "on"):
        return True
    d = d or china_today()
    return d.weekday() >= 5


def weekend_default_count() -> int:
    raw = os.environ.get("AIVIDEO_WEEKEND_MAX_VIDEOS", "3").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 3


def edu_dedup_days() -> int:
    raw = os.environ.get("AIVIDEO_EDU_DEDUP_DAYS", "90").strip()
    try:
        return max(7, int(raw))
    except ValueError:
        return 90


def _norm_title(text: str) -> str:
    t = re.sub(r"\s+", "", (text or "").lower())
    return re.sub(r"[？?！!。，、：:；;「」\"'（）()【】\[\]·\-—]", "", t)


def _titles_overlap(a: str, b: str) -> bool:
    na, nb = _norm_title(a), _norm_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 6 and shorter in longer:
        return True
    # 同一核心概念：去掉「是什么/怎么算」后高度重叠
    core_a = re.sub(r"(是什么|怎么算|怎么看|如何计算|入门|科普)", "", na)
    core_b = re.sub(r"(是什么|怎么算|怎么看|如何计算|入门|科普)", "", nb)
    if len(core_a) >= 4 and core_a == core_b:
        return True
    return False


def _topic_id_from_title(title: str) -> str:
    norm = _norm_title(title)[:48]
    digest = hashlib.md5(norm.encode("utf-8")).hexdigest()[:10]
    return f"edu_{digest}"


def _recent_titles_for_pick() -> list[str]:
    from batch_aivideo import published_titles_for_dedup

    return published_titles_for_dedup(days=edu_dedup_days(), limit=80)


def _validate_pick(row: dict, *, avoid: list[str], batch_titles: list[str]) -> str:
    title = str(row.get("title") or "").strip()
    if not title or len(title) < 6:
        return "标题过短或为空"
    cat = str(row.get("category") or "").strip().lower()
    if cat not in EDU_CATEGORY_ORDER:
        return f"无效 category: {cat}"
    for prev in avoid + batch_titles:
        if _titles_overlap(title, prev):
            return f"与已做话题过于相似：{prev[:40]}"
    return ""


def _opus_pick_topics(
    *,
    count: int,
    category: str | None = None,
    extra_reject: list[str] | None = None,
) -> list[dict]:
    """让 Opus 根据历史动态提出科普话题。"""
    load_env()
    recent = _recent_titles_for_pick()
    reject = list(extra_reject or [])
    max_attempts = max(2, int(os.environ.get("AIVIDEO_EDU_PICK_ATTEMPTS", "3")))

    category_hint = ""
    if category and category in EDU_CATEGORY_ORDER:
        category_hint = f"【指定栏目】必须属于 {category}（{EDU_SLOT_LABEL[category]}）\n"

    for attempt in range(max_attempts):
        reject_block = ""
        if reject:
            reject_block = (
                "\n【上一轮不合格，必须避开】\n"
                + "\n".join(f"- {t}" for t in reject[-12:])
                + "\n"
            )
        recent_lines = "\n".join(f"- {t}" for t in recent[:60]) if recent else "（暂无历史，可自由选题）"
        user = EDU_PICK_USER.format(
            today=china_today().isoformat(),
            count=count,
            category_hint=category_hint,
            dedup_days=edu_dedup_days(),
            recent_titles=recent_lines,
            reject_block=reject_block,
        )
        print(f"  🎯 {text_model()} 周末科普选题（{count} 条，历史 {len(recent)} 条）…", flush=True)
        raw = chat_complete(
            system=EDU_PICK_SYSTEM,
            user=user,
            max_tokens=4000,
            response_format_json=True,
        )
        data = extract_json(raw)
        rows = data.get("topics") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            reject.append(f"attempt{attempt}: 无效 JSON")
            continue

        picked: list[dict] = []
        batch_titles: list[str] = []
        errors: list[str] = []
        for row in rows[: count + 2]:
            if not isinstance(row, dict):
                continue
            err = _validate_pick(row, avoid=recent, batch_titles=batch_titles)
            if err:
                t = str(row.get("title") or "")
                errors.append(f"{t}: {err}")
                if t:
                    reject.append(t)
                continue
            title = str(row["title"]).strip()
            cat = str(row["category"]).strip().lower()
            picked.append({
                "title": title,
                "category": cat,
                "theme_cluster": str(row.get("theme_cluster") or f"edu_{cat}").strip(),
                "cold_open": str(row.get("cold_open") or f"今天搞懂：{title.split('，')[0]}").strip(),
                "angle": str(row.get("angle") or EDU_SLOT_LABEL.get(cat, "财经科普")).strip(),
                "reason": str(row.get("reason") or "").strip(),
                "topic_id": _topic_id_from_title(title),
            })
            batch_titles.append(title)
            if len(picked) >= count:
                break

        if len(picked) >= count:
            _save_pick_log(picked, recent_count=len(recent))
            return picked[:count]

        reject.extend(errors)
        print(f"  ⚠️  选题校验未通过（{attempt + 1}/{max_attempts}），重试…", flush=True)

    raise RuntimeError(
        f"Opus 未能提出 {count} 个不重复科普话题（近 {edu_dedup_days()} 天已有 {len(recent)} 条历史）"
    )


def _save_pick_log(topics: list[dict], *, recent_count: int) -> None:
    try:
        from locale_env import locale_logs_dir

        path = locale_logs_dir("zh") / "weekend_edu_last_pick.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "picked_at": datetime.now(timezone.utc).isoformat(),
                    "recent_history_count": recent_count,
                    "topics": topics,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        pass


def topic_plan_for_edu(row: dict) -> dict:
    cat = str(row.get("category") or "basic").strip().lower()
    title = str(row.get("title") or "").strip()
    return {
        "slot": f"edu_{cat}",
        "script_mode": "edu_explain",
        "title_hint": title,
        "suggested_video_title": title,
        "cold_open": row.get("cold_open") or f"今天搞懂：{title.split('，')[0]}",
        "angle": row.get("angle") or EDU_SLOT_LABEL.get(cat, "财经科普"),
        "theme_cluster": row.get("theme_cluster") or f"edu_{cat}",
        "topic_id": row.get("topic_id") or _topic_id_from_title(title),
        "category": "quant" if cat == "quant" else "basic",
        "direction": f"edu_{cat}",
    }


def _row_to_topic(row: dict, *, index: int) -> dict:
    plan = topic_plan_for_edu(row)
    cat = row["category"]
    return {
        "index": index,
        "slot": plan["slot"],
        "direction": plan["direction"],
        "cursor_slot": plan["slot"],
        "title_hint": plan["title_hint"],
        "category": plan["category"],
        "theme_cluster": plan["theme_cluster"],
        "topic_id": plan["topic_id"],
        "angle": plan["angle"],
        "cold_open": plan["cold_open"],
        "script_mode": plan["script_mode"],
        "suggested_video_title": plan["suggested_video_title"],
        "reason": row.get("reason") or f"Opus 选题 · {EDU_SLOT_LABEL.get(cat, cat)}",
        "mode": "weekend_edu",
    }


def discover_weekend_edu_topics(*, target: int | None = None) -> list[dict]:
    """周末科普：Opus 一次提出 target 条不重复话题。"""
    target = target if target is not None else weekend_default_count()
    target = max(1, target)
    recent = _recent_titles_for_pick()
    if recent:
        print(
            f"  📚 科普去重：近 {edu_dedup_days()} 天已有 {len(recent)} 条标题记录",
            flush=True,
        )
    rows = _opus_pick_topics(count=target)
    return [_row_to_topic(row, index=i) for i, row in enumerate(rows, 1)]


def topic_for_edu_slot(slot: str) -> dict:
    """--slot edu_basic / edu_quant / edu_valuation"""
    cat = slot.replace("edu_", "", 1) if slot.startswith("edu_") else slot
    if cat not in EDU_CATEGORY_ORDER:
        raise ValueError(f"未知周末科普槽位: {slot}")
    rows = _opus_pick_topics(count=1, category=cat)
    return _row_to_topic(rows[0], index=1)


def _save_draft(slot: str, markdown: str, meta: dict) -> Path:
    drafts = ROOT / "logs" / "edu_drafts"
    drafts.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = drafts / f"{stamp}_{slot}.md"
    header = (
        f"<!-- slot={slot} mode=weekend_edu model={model_id()} "
        f"agent={meta.get('agent_id')} run={meta.get('run_id')} -->\n\n"
    )
    path.write_text(header + markdown.strip() + "\n", encoding="utf-8")
    meta_path = path.with_suffix(".json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def run_edu_draft(
    topic: dict,
    *,
    agent_id: str | None = None,
    on_assistant=None,
) -> tuple[str, str, str]:
    """Cursor 写科普长文草稿。"""
    title = str(topic.get("title_hint") or topic.get("title") or "").strip()
    slot = str(topic.get("slot") or "edu_basic")
    prompt = (
        f"【当前日期参考】{china_today().isoformat()}\n"
        f"【任务类型】周末财经科普教育（非新闻）\n\n"
        + _EDU_DRAFT_PROMPT.format(title=title)
    )
    print(f"  ☁️  Cursor 科普写稿 · {title} · model={model_id()}", flush=True)
    if agent_id:
        run_id = create_run(agent_id, prompt)
        print(f"     复用 agent={agent_id} 新 run={run_id}", flush=True)
    else:
        agent_id, run_id = create_agent(prompt)
        print(f"     新建 agent={agent_id} run={run_id}", flush=True)

    chunks: list[str] = []

    def _on_delta(t: str) -> None:
        chunks.append(t)
        if on_assistant:
            on_assistant(t)
        else:
            sys.stdout.write(t)
            sys.stdout.flush()

    text, status = run_with_stream(agent_id, run_id, on_assistant=_on_delta)
    body = (text or "".join(chunks)).strip()
    if not body:
        raise RuntimeError(f"Cursor Agent 未返回科普正文（slot={slot} status={status}）")
    return body, agent_id, status


def _extract_title(markdown: str, fallback: str) -> str:
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return re.sub(r"^#+\s*", "", line).strip()[:80] or fallback
    return fallback[:80]


def build_weekend_edu_research(
    topic: dict,
    *,
    agent_id: str | None = None,
    on_assistant=None,
) -> tuple[dict, dict, str | None]:
    """科普 Cursor 草稿 → Opus 深读。"""
    slot = str(topic.get("slot") or "edu_basic")
    title_hint = str(topic.get("title_hint") or "").strip()
    markdown, agent_id, status = run_edu_draft(
        topic, agent_id=agent_id, on_assistant=on_assistant,
    )
    if status != "FINISHED":
        print(f"  ⚠️  Agent 状态={status}，仍尝试用已返回正文继续", file=sys.stderr)

    meta = {
        "slot": slot,
        "mode": "weekend_edu",
        "topic_id": topic.get("topic_id"),
        "status": status,
        "agent_id": agent_id,
        "model": model_id(),
        "title_hint": title_hint,
    }
    draft_path = _save_draft(slot, markdown, meta)
    print(f"  ✓ 科普草稿已保存: {draft_path} ({len(markdown)} 字)")

    plan = topic_plan_for_edu({
        "topic_id": topic.get("topic_id") or _topic_id_from_title(title_hint),
        "title": title_hint,
        "category": slot.replace("edu_", "", 1) if slot.startswith("edu_") else "basic",
        "theme_cluster": str(topic.get("theme_cluster") or ""),
        "cold_open": topic.get("cold_open"),
        "angle": topic.get("angle"),
    })
    for key in (
        "suggested_video_title", "cold_open", "script_mode",
        "title_hint", "angle", "theme_cluster", "topic_id", "category",
    ):
        if topic.get(key):
            plan[key] = topic[key]

    video_title = str(topic.get("suggested_video_title") or title_hint).strip()
    title = video_title or _extract_title(markdown, title_hint)

    article = {
        "title": title,
        "question_title": title_hint,
        "url": f"cursor-edu-draft://{draft_path.name}",
        "site": "cursor-cloud-agent",
        "author": model_id(),
        "published_at": china_today().isoformat(),
        "language": "zh",
        "summary_zh": markdown[:500],
        "thesis": title_hint,
        "key_facts": [],
        "narrative_arc": "财经科普讲解",
        "source_type": f"cursor:edu:{slot}",
        "_cursor_draft": str(draft_path),
        "_compliance_relaxed": True,
        "_topic_plan": plan,
    }
    if video_title:
        article["_suggested_video_title"] = video_title

    print("  🤖 Opus 深读科普草稿（抽取短视频素材）…")
    details, _ = deep_read_article(article, agent_id=None, full_text=markdown)
    return article, details, agent_id


ALL_SLOT_CHOICES = tuple(list(CURSOR_SLOT_ORDER) + [f"edu_{c}" for c in EDU_CATEGORY_ORDER])
