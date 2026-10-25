"""导演稿：口播不动，给每页挑版式、填板书数据、定节奏金句和配音语气，并校验数字和合规。"""

from __future__ import annotations

import json
import os
import re
import sys

from video_compose import split_narration

LAYOUTS = ("bignum", "compare", "bars", "table", "timeline", "flow", "points", "quote")

_DOMAIN = "财经短视频"
_RULE_SAFE = "不写股票代码，不出现荐股、买卖建议、收益承诺。"

_SYSTEM = f"你是{_DOMAIN}的信息图编导，只输出 JSON，不输出任何解释。"

_PROMPT = """把下面这条{domain}的每一页口播，配成「动态 PPT」画面。口播已经定稿，不许改；你只决定每页用什么版式、板书写什么。

可用版式（每页选一个，相邻两页不要重复）：
- bignum：一个核心数字。value(纯数字字符串) unit(≤4字) label(≤14字) cue notes[≤3]{{text≤18字, cue}}
- compare：2-3 项对比。items[2-3]{{label≤8字, value(数字或区间如"3.2-4"), unit≤3字, note≤14字, hot:bool, cue}}
- bars：2-5 个同单位数值比大小。unit(≤3字) bars[2-5]{{label≤8字, value:数字, hot:bool, cue}}
- table：多维度对照。cols[2-3 个列名≤5字] rows[2-5]{{cells[与cols等长, 每格≤10字], hot:bool, cue}}
- timeline：按时间排的事件。items[2-5]{{when≤8字, text≤16字, cue}}
- flow：因果链/传导路径。steps[3-4]{{text≤14字, cue}}
- points：要点卡，没有像样数据时用。items[2-4]{{text≤16字, cue}}
- quote：官方表态或原话。quote≤40字 by≤16字 cue
每页通用可选：title(页标题≤12字，不填就用 headline) lead(≤18字导语，开口就出场，交代这页讲什么) after{{text≤18字的一句话结论, cue}} stamp{{text≤4字的印章字, cue}}

硬规则：
1. 画面上的每个数字都必须原样出现在口播或原文里。不许换算、不许四舍五入、不许编造。拿不准就不用数字，改用 flow/points/quote。
2. 有 2 个以上可比数字的页，优先用 bars/compare/table/bignum，至少一半的页是数据版式；讲观点、讲梗的页，用 quote/points 把那句金句或原话写上屏。
3. cue 是口播分句编号（见每页的 [0] [1] …），元素在说到那一句时出场；同一页内 cue 不递减。画面不能长时间空着：大约每 3-4 句就要有一个元素出场；前几句在铺垫时，用 lead 撑住开头。
4. 板书是提炼，不是照抄口播；每个字段严格守字数上限。
5. {rule_safe}

封面：cover{{lines[2-3 行，每行≤8字，连起来表达标题意思], tags[2-3 个≤6字的话题词], stamp(≤4字)}}

美术指导（每页和封面都要）：art{{scene, focus, move}}
- scene：英文，≤45 词，把这页的事讲成一个有戏剧张力的漫画瞬间：谁（普通人、交易员、工程师、老板、银行柜员等虚构角色）在做什么动作、什么表情，
  或者一个夸张的视觉隐喻（巨浪般的K线砸向城市、天平两端的钱袋、被绳子拴住的气球……）；写清镜头角度（低机位/俯拍/特写）和环境光。
  这页口播里有比喻或梗时，优先把那个梗画出来（如「金子像个不挣钱的亲戚」就画一根金条瘫在沙发上、全家人嫌弃地看着它），让观众看图就笑。
  口播里反复出现的同一个人（如故事主角）要用同样的外形描述（年龄、发型、衣着），前后几页认得出是同一个人。
  不写真实人名、公司名、商标，不画任何文字、数字、图表刻度。
- focus：[x, y]，画面焦点在图里的相对位置（0-1），镜头会朝它推进。
- move：镜头运动，push(推近)/pull(拉远)/pan_left/pan_right/tilt_up/tilt_down 选一个，相邻两页不要一样。
封面的 art 写在 cover 里，要一张海报感的主视觉，而且必须有人脸：一个虚构普通人撞见本期这件事时最夸张的表情
（瞪大眼、张大嘴、捂脸、抓头、捏着账单或手机愣住），手里或身边要有本期话题的具体物件（金镯、房贷合同、油价牌等），
半身到面部特写、略低机位、强烈速度线，focus 放在脸上。观众看封面第一眼就知道讲的是什么、而且这事很离谱。

导演节奏（让整条视频有起伏，别一路平铺）：
- mood：全片配乐情绪，hype(燃)/epic(史诗)/tense(悬疑紧张)/warm(温情) 选一个。
- 每页 energy：1-5，配乐和动效强度。要有起伏：铺垫页 2-3，高潮页 5，高潮后可以回落一下再收尾。
- 每页 enter：进入这页的转场，ink(漫画斜切擦屏)/whip(快速横甩)/zoom(冲镜头)/flash(白闪) 选一个，相邻不重复；energy 5 的页用 flash 或 zoom。
- 每页可选 peak{{text≤10字, cue}}：这页的气氛点兼记忆点。到那一句时白闪、震屏、重低音，一条金句砸到屏幕上。金句要短、有冲击力、观众看一眼就记住（例如「一年少还7163亿」「我这口价不能松」），全片 3-5 个，大约每两页一个，放在观点句、反转句、梗的笑点上，不要每页都有。
- recap：结尾「今天记住」卡，2-3 条，每条≤8字，是全片最该记住的数字或结论。
- 封面上念的是开场白（抛出本期问题），念到「封面大字」那句原话时大字会砸到封面插图上，这个不用你写。
  cover.tone 是开场白的配音语气：像主播开口就抛出一个大家都想问的问题，好奇里带点调侃，节奏要快。

配音导演（让口播像真人讲故事，有起伏、有突然的情绪，而不是念稿）：
- cover.tone 和每页 tone：≤30字的语气指令，告诉配音这一段整体怎么说，要跟内容走，相邻两段不要一样。
  例：「像发现了一件离谱的事，惊讶里带点调侃」「放慢、压低声音，像在揭一个秘密」「笃定有力，一字一顿」「替观众打抱不平，有点气」「憋着笑损一句」。
- 每页 marks[0-3]{{cue, tag≤6字}}：只给真正的情绪转折句打标签，tag 只描述语气，例如 惊讶/难以置信/激动/扎心/调侃/压低声音/斩钉截铁/恍然大悟。
  反常数字、反转、金句、梗的笑点那一句最该打（梗用 调侃/憋笑/损一句）；平铺直叙的句子不打，同一页标签别重复。

只输出 JSON：{{"mood": "...", "cover": {{..., "tone": "..."}}, "slides": [{{"layout": "...", "energy": 3, "enter": "...", "tone": "...", "marks": [...], ...}}, ...], "recap": [...]}}，slides 与下面页数一一对应。

视频标题：{title}
开场白（封面上念）：{cold_open}
封面大字（开场白里的原话）：{hook_line}

{pages}

原文（事实来源）：
{article}
"""


def phrases_of(text: str) -> list[str]:
    return split_narration(text) or [text.strip() or "…"]


def _source_numbers(texts: list[str]) -> set[str]:
    found = set()
    for t in texts:
        for n in re.findall(r"\d+(?:\.\d+)?", t or ""):
            found.add(_norm_num(n))
    return found


def _norm_num(n: str) -> str:
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n.lstrip("0") or "0"


def _clip(text, limit: int) -> str:
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    return t if len(t) <= limit else t[:limit]


class _Checker:
    def __init__(self, script: dict, article_text: str):
        texts = [article_text, str(script.get("title") or ""), str(script.get("cold_open") or ""),
                 str(script.get("hook_line") or "")]
        for s in script.get("slides") or []:
            texts.append(str(s.get("narration") or ""))
            texts.extend(str(x) for x in s.get("on_image_text") or [])
        self.numbers = _source_numbers(texts)
        self.script = script
        self.dropped: list[str] = []
        try:
            import research

            self._block = tuple(research._DOUYIN_SENSITIVE_BLOCK) + tuple(research._reco_banned_for(script=script))
            self._codes = research._STOCK_CODE_PATTERNS
        except Exception:
            self._block, self._codes = (), ()

    def ok(self, *texts) -> bool:
        for raw in texts:
            t = str(raw or "")
            bad = [n for n in re.findall(r"\d+(?:\.\d+)?", t) if _norm_num(n) not in self.numbers]
            if bad:
                self.dropped.append(f"无出处数字 {bad}：{t}")
                return False
            if any(p in t for p in self._block) or any(p.search(t) for p in self._codes):
                self.dropped.append(f"合规拦截：{t}")
                return False
        return True


def _cue(raw, n: int, floor: int) -> int:
    try:
        k = int(raw)
    except (TypeError, ValueError):
        k = floor
    return max(floor, min(k, n - 1))


def _fallback(slide: dict, n: int) -> dict:
    items = [str(x).strip() for x in slide.get("on_image_text") or [] if str(x).strip()][:4]
    if not items:
        items = [_clip(slide.get("headline"), 16)]
    step = max(1, n // (len(items) + 1))
    return {
        "layout": "points",
        "items": [{"text": _clip(x, 16), "cue": min(n - 1, (i + 1) * step)} for i, x in enumerate(items)],
    }


def _clean_slide(raw: dict, slide: dict, n: int, chk: _Checker) -> dict:
    layout = raw.get("layout")
    if layout not in LAYOUTS:
        return _fallback(slide, n)
    out: dict = {"layout": layout}
    floor = 0

    def take(seq, limit, build):
        nonlocal floor
        res = []
        for it in (seq or [])[:limit]:
            if not isinstance(it, dict):
                continue
            item = build(it)
            if item is None:
                continue
            item["cue"] = floor = _cue(it.get("cue"), n, floor)
            res.append(item)
        return res

    if layout == "bignum":
        value = re.sub(r"[^\d.]", "", str(raw.get("value") or ""))
        if not value or not chk.ok(value, raw.get("label")):
            return _fallback(slide, n)
        out.update(value=value, unit=_clip(raw.get("unit"), 4), label=_clip(raw.get("label"), 14))
        out["cue"] = floor = _cue(raw.get("cue"), n, 0)
        out["notes"] = take(
            raw.get("notes"), 3, lambda it: {"text": _clip(it.get("text"), 18)} if chk.ok(it.get("text")) else None
        )
    elif layout == "compare":
        def item(it):
            v = _clip(it.get("value"), 9)
            if not v or not chk.ok(v, it.get("label"), it.get("note")):
                return None
            return {
                "label": _clip(it.get("label"), 8),
                "value": v,
                "unit": _clip(it.get("unit"), 3),
                "note": _clip(it.get("note"), 14),
                "hot": bool(it.get("hot")),
            }

        out["items"] = take(raw.get("items"), 3, item)
        if len(out["items"]) < 2:
            return _fallback(slide, n)
    elif layout == "bars":
        def bar(it):
            try:
                v = float(it.get("value"))
            except (TypeError, ValueError):
                return None
            if v < 0 or not chk.ok(str(it.get("value")), it.get("label")):
                return None
            return {"label": _clip(it.get("label"), 8), "value": v, "text": str(it.get("value")), "hot": bool(it.get("hot"))}

        out["unit"] = _clip(raw.get("unit"), 3)
        out["bars"] = take(raw.get("bars"), 5, bar)
        if len(out["bars"]) < 2 or max(b["value"] for b in out["bars"]) <= 0:
            return _fallback(slide, n)
    elif layout == "table":
        cols = [_clip(c, 5) for c in raw.get("cols") or []][:3]
        if len(cols) < 2:
            return _fallback(slide, n)

        def row(it):
            cells = [_clip(c, 10) for c in it.get("cells") or []][: len(cols)]
            if len(cells) != len(cols) or not chk.ok(*cells):
                return None
            return {"cells": cells, "hot": bool(it.get("hot"))}

        out["cols"] = cols
        out["rows"] = take(raw.get("rows"), 5, row)
        if len(out["rows"]) < 2:
            return _fallback(slide, n)
    elif layout == "timeline":
        out["items"] = take(
            raw.get("items"),
            5,
            lambda it: {"when": _clip(it.get("when"), 8), "text": _clip(it.get("text"), 16)}
            if chk.ok(it.get("when"), it.get("text"))
            else None,
        )
        if len(out["items"]) < 2:
            return _fallback(slide, n)
    elif layout == "flow":
        out["steps"] = take(
            raw.get("steps"), 4, lambda it: {"text": _clip(it.get("text"), 14)} if chk.ok(it.get("text")) else None
        )
        if len(out["steps"]) < 2:
            return _fallback(slide, n)
    elif layout == "points":
        out["items"] = take(
            raw.get("items"), 4, lambda it: {"text": _clip(it.get("text"), 16)} if chk.ok(it.get("text")) else None
        )
        if not out["items"]:
            return _fallback(slide, n)
    elif layout == "quote":
        q = _clip(raw.get("quote"), 40)
        if not q or not chk.ok(q, raw.get("by")):
            return _fallback(slide, n)
        out.update(quote=q, by=_clip(raw.get("by"), 16), cue=_cue(raw.get("cue"), n, 0))

    title = _clip(raw.get("title"), 12)
    if title and chk.ok(title):
        out["title"] = title
    lead = _clip(raw.get("lead"), 18)
    if lead and chk.ok(lead):
        out["lead"] = lead
    _pull_first_cue(out)
    for key, limit in (("after", 18), ("stamp", 4)):
        box = raw.get(key)
        if isinstance(box, dict) and box.get("text") and chk.ok(box.get("text")):
            out[key] = {"text": _clip(box["text"], limit), "cue": _cue(box.get("cue"), n, 0)}
    art = _clean_art(raw.get("art"))
    if art:
        out["art"] = art
    try:
        out["energy"] = max(1, min(5, int(raw.get("energy"))))
    except (TypeError, ValueError):
        pass
    if raw.get("enter") in ENTERS:
        out["enter"] = raw["enter"]
    peak = raw.get("peak")
    if isinstance(peak, dict) and str(peak.get("text") or "").strip() and chk.ok(peak.get("text")):
        out["peak"] = {"text": _clip(peak["text"], 10), "cue": _cue(peak.get("cue"), n, 0)}
    out.update(_clean_voice(raw, n))
    return out


def _clean_voice(raw: dict, n: int) -> dict:
    """配音语气：tone 是整段的语气指令，marks 是给某几句单独加的情绪标签（同一句只留第一个）。"""
    out: dict = {}
    tone = _clip(raw.get("tone"), 30)
    if tone:
        out["tone"] = tone
    marks, seen = [], set()
    for it in raw.get("marks") or []:
        if not isinstance(it, dict):
            continue
        tag = re.sub(r"[<>=\s]", "", _clip(it.get("tag"), 6))
        k = _cue(it.get("cue"), n, 0)
        if tag and k not in seen:
            seen.add(k)
            marks.append({"cue": k, "tag": tag})
    if marks:
        out["marks"] = sorted(marks, key=lambda m: m["cue"])[:3]
    return out


ENTERS = ("ink", "whip", "zoom", "flash")
MOODS = ("hype", "epic", "tense", "warm")


def _direct(planned: list[dict]) -> None:
    """模型没给或给得不合规时补齐节奏：能量有起伏、转场不重复、金句最多 3 个。"""
    curve = [3, 4, 5, 4, 5, 3]
    prev = "ink"
    for i, p in enumerate(planned):
        p.setdefault("energy", curve[i % len(curve)] if i else 3)
        if p.get("enter") not in ENTERS or (i and p["enter"] == prev):
            p["enter"] = next(e for e in ENTERS[i % 4:] + ENTERS if e != prev)
        prev = p["enter"]
    peaks = sorted((p for p in planned if p.get("peak")), key=lambda p: -p["energy"])
    for p in peaks[max(3, (len(planned) + 1) // 2):]:
        p.pop("peak")


MOVES = ("push", "pull", "pan_left", "pan_right", "tilt_up", "tilt_down")


def _clean_art(raw) -> dict | None:
    if not isinstance(raw, dict) or not str(raw.get("scene") or "").strip():
        return None
    try:
        fx, fy = (min(1.0, max(0.0, float(v))) for v in (raw.get("focus") or [0.5, 0.4])[:2])
    except (TypeError, ValueError):
        fx, fy = 0.5, 0.4
    move = raw.get("move") if raw.get("move") in MOVES else "push"
    return {"scene": _clip(raw.get("scene"), 400), "focus": [round(fx, 3), round(fy, 3)], "move": move}


def planner_model() -> str:
    return os.environ.get("AIVIDEO_EXPLAINER_MODEL", "").strip() or "claude-opus-5-5"


def _pull_first_cue(out: dict) -> None:
    """没有导语时，第一个元素最晚在第 3 句出场，免得开头长时间空屏。"""
    if out.get("lead"):
        return
    seq = next((out[k] for k in ("notes", "items", "bars", "rows", "steps") if out.get(k)), None)
    first = out if "cue" in out and out["layout"] in ("bignum", "quote") else (seq[0] if seq else None)
    if first and first.get("cue", 0) > 2:
        first["cue"] = 2


def _clean_cover(raw: dict, script: dict, chk: _Checker) -> dict:
    lines = [_clip(x, 9) for x in raw.get("lines") or [] if str(x).strip()][:3]
    if len(lines) < 2 or not chk.ok(*lines):
        parts = [p for p in re.split(r"[，。？！?!,\s]+", str(script.get("title") or "")) if p]
        lines = [_clip(p, 9) for p in parts[:3]] or [_clip(script.get("title"), 9)]
    tags = [_clip(x, 6) for x in raw.get("tags") or script.get("hashtags") or [] if str(x).strip()][:3]
    stamp = _clip(raw.get("stamp"), 4) if chk.ok(raw.get("stamp")) else ""
    out = {"lines": lines, "tags": [t for t in tags if chk.ok(t)], "stamp": stamp}
    tone = _clip(raw.get("tone"), 30)
    if tone:
        out["tone"] = tone
    return out


def _clean_hook(script: dict, planned: list[dict], recap: list[str], chk: _Checker) -> dict | None:
    """砸到封面上的大字：用稿子里的 hook_line（开场白原话）；没有或不合规时取能量最高那页的金句。"""
    text = str(script.get("hook_line") or "").strip()
    if not text or len(text) > 14 or not chk.ok(text):
        peaks = sorted((p for p in planned if p.get("peak")), key=lambda p: -p.get("energy", 3))
        text = peaks[0]["peak"]["text"] if peaks else (recap[0] if recap else "")
    if not text:
        return None
    return {"text": text}


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("模型没有返回 JSON")
    return json.loads(m.group(0))


def article_text(data: dict, script: dict) -> str:
    art = data.get("article") or script.get("article") or {}
    if not isinstance(art, dict):
        return ""
    parts = [str(art.get("summary_zh") or "")]
    parts.extend(str(x) for x in art.get("key_facts") or [])
    return "\n".join(parts)


def plan_visuals(data: dict, script: dict) -> dict:
    slides = script.get("slides") or []
    art = article_text(data, script)
    chk = _Checker(script, art)
    pages = []
    for i, s in enumerate(slides, 1):
        ph = phrases_of(str(s.get("narration") or ""))
        numbered = "\n".join(f"  [{k}] {p}" for k, p in enumerate(ph))
        pages.append(
            f"第{i}页 headline：{s.get('headline')}\n"
            f"  上屏要点：{'、'.join(map(str, s.get('on_image_text') or []))}\n"
            f"  口播分句：\n{numbered}"
        )
    prompt = _PROMPT.format(
        domain=_DOMAIN,
        rule_safe=_RULE_SAFE,
        title=script.get("title") or "",
        hook_line=script.get("hook_line") or script.get("cold_open") or "",
        cold_open=script.get("cold_open") or "",
        pages="\n\n".join(pages),
        article=art[: int(os.environ.get("AIVIDEO_EXPLAINER_ARTICLE_CHARS", "6000"))],
    )
    raw: dict = {}
    try:
        from text_client import chat_complete

        raw = _parse_json(chat_complete(system=_SYSTEM, user=prompt, model=planner_model(), max_tokens=8000))
    except Exception as exc:
        print(f"[explainer] 版式规划失败，全部退回要点卡：{exc}", file=sys.stderr)
    raw_slides = raw.get("slides") if isinstance(raw.get("slides"), list) else []
    planned = []
    for i, s in enumerate(slides):
        n = len(phrases_of(str(s.get("narration") or "")))
        r = raw_slides[i] if i < len(raw_slides) and isinstance(raw_slides[i], dict) else {}
        planned.append(_clean_slide(r, s, n, chk))
    raw_cover = raw.get("cover") if isinstance(raw.get("cover"), dict) else {}
    cover = _clean_cover(raw_cover, script, chk)
    art = _clean_art(raw_cover.get("art"))
    if art:
        cover["art"] = art
    _direct(planned)
    recap = [_clip(x, 8) for x in raw.get("recap") or [] if str(x).strip() and chk.ok(x)][:3]
    mood = raw.get("mood") if raw.get("mood") in MOODS else "hype"
    hook = _clean_hook(script, planned, recap, chk)
    for note in chk.dropped:
        print(f"[explainer] 丢弃：{note}", file=sys.stderr)
    print(f"[explainer] 导演（{planner_model()}）：配乐 {mood}；"
          + " / ".join(f"{p['layout']}·能量{p['energy']}·{p['enter']}" + (f"·金句「{p['peak']['text']}」" if p.get("peak") else "")
                       for p in planned), file=sys.stderr)
    if hook:
        print(f"[explainer] 封面大字：「{hook['text']}」", file=sys.stderr)
    print("[explainer] 配音语气：" + " / ".join(
        (p.get("tone") or "-") + "".join(f"［{m['cue']}:{m['tag']}］" for m in p.get("marks") or [])
        for p in [cover, *planned]), file=sys.stderr)
    return {"mood": mood, "hook": hook, "cover": cover, "slides": planned, "recap": recap if len(recap) >= 2 else []}
