#!/usr/bin/env python3
"""一键制作并发布：Exa 选题 → 改编 → 生图 → 合成 → 抖音发布 → 归档。"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cost_tracker

from batch_aivideo import (
    append_history_from_script,
    duplicate_topic_reason,
    filter_duplicate_topics,
    history_exclude_urls,
    history_recent_topics,
)
from paths import ROOT
from publish_all_douyin import load_published, save_published
from research import find_articles, load_env, run_article_research, score_articles


def log(message: str) -> None:
    print(message, flush=True)


# ============================================================
# 选题方向分桶：保证一次产出覆盖 A股 / AI / 港美股 三个方向
# ============================================================
DIRECTION_ORDER = ("astock", "ai", "hkus")
DIRECTION_LABEL = {"astock": "A股", "ai": "AI", "hkus": "港美股"}

_ASTOCK_KW = re.compile(
    r"a股|涨停|跌停|龙虎榜|游资|科创板|创业板|北向|北交所|沪深|沪指|深成指|创业板指|"
    r"科创50|连板|妖股|人气股|打板|涨停板|主力资金|两市|沪市|深市|题材股|概念股|集合竞价",
    re.I,
)
_HKUS_KW = re.compile(
    r"美股|港股|中概|纳斯达克|纳指|道指|标普|nasdaq|nyse|s&p|hong kong|hkex|"
    r"七姐妹|magnificent|wall street|华尔街",
    re.I,
)


def direction_bucket(cand: dict) -> str:
    """把候选归到 astock / ai / hkus 三个方向之一。

    优先级：A股专源/标记/关键词 → AI → 港美股（含中概股）。
    """
    st = str(cand.get("source_type") or "")
    cat = str(cand.get("category") or "").lower()
    text = " ".join(
        str(cand.get(k) or "")
        for k in ("title", "question_title", "summary_zh", "summary_en", "thesis", "site")
    ).lower()
    if st == "exa:astock" or cat == "astock" or (_ASTOCK_KW.search(text) and not _HKUS_KW.search(text)):
        return "astock"
    if cat == "ai":
        return "ai"
    if cat in {"earnings", "stock", "finance", "macro"}:
        return "hkus"
    # mixed / 未知：有港美股信号归港美股，否则归 AI。
    return "hkus" if _HKUS_KW.search(text) else "ai"


def _interleave_by_direction(cands: list[dict]) -> list[dict]:
    """按方向轮转交错（A股 → AI → 港美股 → …），保留各方向内部原有顺序。"""
    buckets: dict[str, list[dict]] = {d: [] for d in DIRECTION_ORDER}
    for c in cands:
        buckets[direction_bucket(c)].append(c)
    out: list[dict] = []
    while any(buckets[d] for d in DIRECTION_ORDER):
        for d in DIRECTION_ORDER:
            if buckets[d]:
                out.append(buckets[d].pop(0))
    return out


# 理想配额（按 target=5 设计）：A股 爆点为主，AI / 港美股 各 1 条点缀。
# 可用 AIVIDEO_DIR_QUOTA 覆盖，格式如 "astock:3,ai:1,hkus:1"。
DIRECTION_BASE_QUOTA = {"astock": 3, "ai": 1, "hkus": 1}


def _base_quota() -> dict[str, int]:
    raw = os.environ.get("AIVIDEO_DIR_QUOTA", "").strip()
    if not raw:
        return dict(DIRECTION_BASE_QUOTA)
    base = {d: 0 for d in DIRECTION_ORDER}
    for part in raw.split(","):
        if ":" not in part:
            continue
        k, _, v = part.partition(":")
        k = k.strip()
        if k in base:
            try:
                base[k] = max(0, int(v.strip()))
            except ValueError:
                pass
    return base if any(base.values()) else dict(DIRECTION_BASE_QUOTA)


def direction_quotas(target: int, present: list[str]) -> dict[str, int]:
    """在「实际有候选的方向」间按 A股优先的加权方式分配目标条数。

    target=5 且三方向都有 → A股 3 / AI 1 / 港美股 1（DIRECTION_BASE_QUOTA）。
    某方向缺候选时名额按优先级顺延给其它方向；target 与基准不一致时按优先级增减。
    """
    order = [d for d in DIRECTION_ORDER if d in present]
    quotas = {d: 0 for d in DIRECTION_ORDER}
    if not order or target <= 0:
        return quotas
    n = len(order)
    base = _base_quota()
    remaining = target
    # 1) 方向数 <= 目标：先保证每个在场方向各 1 条作为下限
    if target >= n:
        for d in order:
            quotas[d] = 1
        remaining -= n
    # 2) 剩余名额按基准权重优先补给（A股 先补到基准，再 AI、港美股）
    while remaining > 0:
        progressed = False
        for d in order:
            if remaining <= 0:
                break
            if quotas[d] < base.get(d, 0):
                quotas[d] += 1
                remaining -= 1
                progressed = True
        if not progressed:
            break
    # 3) 仍有剩余（target 超过基准总额）：全部压给最高优先级在场方向（A股）
    while remaining > 0:
        quotas[order[0]] += 1
        remaining -= 1
    return quotas


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve()))


def run(cmd: list[str], *, label: str) -> None:
    log(f"\n[{label}] {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT, env=os.environ.copy())
    if proc.returncode != 0:
        raise RuntimeError(f"{label} 失败，退出码 {proc.returncode}")


def read_script_title(script_path: Path) -> str:
    try:
        data = json.loads(script_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    script = data.get("script") or data
    return str(script.get("title") or "").strip()


def latest_video() -> Path:
    last_video = ROOT / "logs" / "last_video.txt"
    if not last_video.is_file():
        raise RuntimeError("未找到 logs/last_video.txt")
    raw = last_video.read_text(encoding="utf-8").strip()
    video = Path(raw)
    if not video.is_absolute():
        video = ROOT / video
    if not video.is_file():
        raise RuntimeError(f"视频文件不存在: {video}")
    return video


# ============================================================
# 多平台联动发布：抖音成功后顺手发小红书（best-effort，失败不阻断）
# ============================================================
# 平台 → 开关环境变量；小红书默认开启（"两个媒体"=抖音+小红书），其余默认关闭。
SOCIAL_PLATFORMS = {
    "xiaohongshu": ("AIVIDEO_PUBLISH_XHS", True),
    "kuaishou": ("AIVIDEO_PUBLISH_KS", False),
    "shipinhao": ("AIVIDEO_PUBLISH_SHIPINHAO", False),
}
SOCIAL_LABEL = {"xiaohongshu": "小红书", "kuaishou": "快手", "shipinhao": "视频号"}


def _social_enabled(platform: str) -> bool:
    env_key, default = SOCIAL_PLATFORMS[platform]
    value = os.environ.get(env_key)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _social_gap_seconds() -> int:
    """平台之间的随机间隔（秒），模拟真人发布节奏、降低风控误杀。
    用 AIVIDEO_SOCIAL_GAP_MIN / AIVIDEO_SOCIAL_GAP_MAX 调整（默认 45–120 秒）。"""
    try:
        lo = int(os.environ.get("AIVIDEO_SOCIAL_GAP_MIN", "45"))
        hi = int(os.environ.get("AIVIDEO_SOCIAL_GAP_MAX", "120"))
    except ValueError:
        lo, hi = 45, 120
    lo = max(0, lo)
    hi = max(lo, hi)
    return random.randint(lo, hi)


def publish_social(video: Path, script_path: Path) -> None:
    """把已发抖音的同一条视频顺手发到启用的其它平台。任一平台失败只告警，不影响主流程。

    平台之间加入随机间隔（拟人化），避免无间隔连发被风控误判为脚本批量操作。"""
    from backfill_social import load_platform_published, save_platform_published

    attempted = 0
    for platform in SOCIAL_PLATFORMS:
        if not _social_enabled(platform):
            continue
        label = SOCIAL_LABEL[platform]
        done = load_platform_published(platform)
        if video.name in done:
            log(f"  [{label}] 已发过，跳过")
            continue
        if attempted > 0:
            gap = _social_gap_seconds()
            log(f"  ⏳ 拟人化间隔 {gap}s 后再发{label}…")
            time.sleep(gap)
        attempted += 1
        try:
            cmd = [
                str(ROOT / "scripts" / "publish-social.sh"),
                platform,
                rel(video),
                "--script",
                rel(script_path),
            ]
            run(cmd, label=f"发布{label}")
            done.add(video.name)
            save_platform_published(platform, done)
            log(f"  [{label}] 发布成功")
        except Exception as exc:  # noqa: BLE001
            log(f"  ⚠️ [{label}] 发布失败（不影响抖音/主流程）：{exc}")


def archive_video(video: Path, *, date_tag: str) -> Path:
    dest_dir = ROOT / "archive" / "published" / date_tag
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / video.name
    if target.exists():
        target = dest_dir / f"{video.stem}_{datetime.now().strftime('%H%M%S')}{video.suffix}"
    shutil.move(str(video), str(target))
    return target


def select_topic_pool(*, days: int) -> list[dict]:
    """返回**所有过线的候选**（按分降序），上层按需逐条尝试，失败就换下一条。"""
    exclude = history_exclude_urls()
    recent_topics = history_recent_topics(limit=80)
    if recent_topics:
        log(f"已加载历史标题 {len(recent_topics)} 条用于去重")

    log(f"\n=== 选题打分：Exa 近 {days} 天 ===")
    candidates, _ = find_articles(
        days=days,
        exclude_urls=exclude,
        recent_topics=recent_topics,
        source="exa",
    )
    candidates = filter_duplicate_topics(candidates)
    if not candidates:
        log("候选均命中近 7 天本地去重，本次不制作。")
        return []
    # 打分只覆盖前 N 个候选（AIVIDEO_SCORE_MAX_CANDIDATES，默认 40），而 A股 池是追加在最后的。
    # 这里按方向轮转交错（A股 优先打头），确保三方向都进入打分窗口，A股 不再被挤出。
    candidates = _interleave_by_direction(candidates)
    pre = {d: 0 for d in DIRECTION_ORDER}
    for c in candidates:
        pre[direction_bucket(c)] += 1
    log(f"  候选方向分布：A股 {pre['astock']}，AI {pre['ai']}，港美股 {pre['hkus']}（已交错排序送打分）")
    scored, decision = score_articles(candidates, recent_topics=recent_topics)
    scored = filter_duplicate_topics(scored)

    for cand in scored:
        cand["direction"] = direction_bucket(cand)

    report = ROOT / "logs" / "make_publish_topics.json"
    report.write_text(
        json.dumps(
            {
                "days": days,
                "threshold": decision.get("threshold"),
                "candidate_count": len(candidates),
                "accepted_count": len(scored),
                "pool": scored,
                "decision": decision,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    by_dir: dict[str, int] = {d: 0 for d in DIRECTION_ORDER}
    for cand in scored:
        by_dir[cand.get("direction", "ai")] = by_dir.get(cand.get("direction", "ai"), 0) + 1
    dir_brief = "，".join(f"{DIRECTION_LABEL[d]} {by_dir[d]}" for d in DIRECTION_ORDER)
    log(f"选题完成：{len(candidates)} 个候选，{len(scored)} 个过线进入候选池（{dir_brief}）")
    for i, topic in enumerate(scored, 1):
        tag = DIRECTION_LABEL.get(topic.get("direction", ""), "?")
        log(f"  {i}. [{tag}|{topic.get('topic_score')}] {topic.get('question_title') or topic.get('title')}")
    return scored


def process_one(
    index: int,
    *,
    target: int,
    days: int,
    publish_check: bool,
    dry_run: bool,
    article: dict,
) -> dict:
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    script_path = logs_dir / f"last_script_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{index:02d}.json"

    log(f"\n=== [{index}/{target}] 制作视频 ===")
    duplicate_reason = duplicate_topic_reason(article)
    if duplicate_reason:
        title = article.get("question_title") or article.get("title")
        raise RuntimeError(f"近 7 天重复选题：{title}（{duplicate_reason}）")
    script, _ = run_article_research(
        output=script_path,
        days=days,
        auto_pick=True,
        recent_topics=history_recent_topics(limit=80),
        source="exa",
        preselected_article=article,
    )
    title = str(script.get("title") or read_script_title(script_path) or "").strip()
    log(f"选题脚本：{title}")

    run([str(ROOT / "scripts" / "run-enrich-images.sh"), str(script_path)], label="生图")
    run([str(ROOT / "scripts" / "run-compose.sh"), str(script_path)], label="合成")
    video = latest_video()

    log(f"\n=== [{index}/{target}] 发布抖音 ===")
    publish_cmd = [str(ROOT / "scripts" / "publish-douyin.sh"), rel(video), "--script", rel(script_path)]
    if publish_check:
        publish_cmd.append("--check")
    if dry_run:
        publish_cmd.append("--dry-run")
    run(publish_cmd, label="发布")

    if dry_run:
        return {"title": title, "video": rel(video), "script": rel(script_path), "published": False}

    published = load_published()
    video_rel = rel(video)
    published.add(video_rel)
    save_published(published)
    append_history_from_script(script_path)

    archived = archive_video(video, date_tag=datetime.now().strftime("%Y%m%d"))
    log(f"发布成功，已记录标题并归档：{rel(archived)}")

    log(f"\n=== [{index}/{target}] 联动发布其它平台 ===")
    publish_social(archived, script_path)

    return {
        "title": title,
        "video": rel(archived),
        "script": rel(script_path),
        "published": True,
    }


def main() -> int:
    load_env()
    os.environ["AIVIDEO_SOURCE"] = "exa"
    parser = argparse.ArgumentParser(description="AI财知道：一键制作并自动发布")
    parser.add_argument("--count", type=int, default=int(os.environ.get("AIVIDEO_MAX_VIDEOS_PER_RUN", "5")),
                        help="本次需要成功制作并发布的视频数（任一条失败自动跳到下一候选）")
    parser.add_argument("--days", type=int, default=int(os.environ.get("AIVIDEO_DAYS", os.environ.get("DAILY_RUN_DAYS", "1"))))
    parser.add_argument("--check", action="store_true", help="发布前检查抖音登录态")
    parser.add_argument("--dry-run", action="store_true", help="只预演发布参数，不真正发布/归档")
    args = parser.parse_args()

    pool = select_topic_pool(days=args.days)
    if not pool:
        log("没有过线选题，本次不制作。")
        return 0

    target = max(1, args.count)
    present = [d for d in DIRECTION_ORDER if any(a.get("direction") == d for a in pool)]
    quotas = direction_quotas(target, present)
    quota_brief = "，".join(
        f"{DIRECTION_LABEL[d]} {quotas[d]}" for d in DIRECTION_ORDER if quotas[d] > 0
    )
    log(f"\n目标成功 {target} 条（按方向配额：{quota_brief}）；候选池共 {len(pool)} 条。")
    log("先按方向配额各取最高分，缺口再用剩余候选回填，失败自动换下一条。")

    run_start = time.time()
    made: list[dict] = []
    failed: list[dict] = []
    used_urls: set[str] = set()
    made_by_dir: dict[str, int] = {d: 0 for d in DIRECTION_ORDER}
    attempt_no = 0

    def try_article(article: dict, *, respect_quota: bool) -> bool:
        """尝试制作+发布一条；成功返回 True。respect_quota=True 时只做未满配额的方向。"""
        nonlocal attempt_no
        if len(made) >= target:
            return False
        url = str(article.get("url") or "").strip()
        if url and url in used_urls:
            return False
        d = article.get("direction", "ai")
        if respect_quota and made_by_dir.get(d, 0) >= quotas.get(d, 0):
            return False
        title = article.get("question_title") or article.get("title")
        attempt_no += 1
        log(f"\n>>> 尝试 #{attempt_no} [{DIRECTION_LABEL.get(d, '?')}|{article.get('topic_score')}] {title}")
        used_urls.add(url)
        try:
            result = process_one(
                len(made) + 1,
                target=target,
                days=args.days,
                publish_check=args.check,
                dry_run=args.dry_run,
                article=article,
            )
            result["direction"] = d
            made.append(result)
            made_by_dir[d] = made_by_dir.get(d, 0) + 1
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"\n✗ 候选失败 [{DIRECTION_LABEL.get(d, '?')}|{article.get('topic_score')}] {title}：{exc}")
            failed.append({
                "title": title,
                "url": article.get("url"),
                "score": article.get("topic_score"),
                "direction": d,
                "error": str(exc),
            })
            return False

    # 阶段一：按方向配额，各方向取最高分（保证 A股/AI/港美股 都覆盖）。
    for article in pool:
        if len(made) >= target:
            break
        try_article(article, respect_quota=True)
    # 阶段二：仍有缺口（某方向无候选或全失败）→ 放开配额，用剩余候选按分回填。
    if len(made) < target:
        log(f"\n[回填] 配额阶段产出 {len(made)}/{target} 条，放开方向限制继续补足…")
        for article in pool:
            if len(made) >= target:
                break
            try_article(article, respect_quota=False)

    summary = ROOT / "logs" / "make_publish_last.json"
    summary.write_text(
        json.dumps(
            {
                "target": target,
                "made": made,
                "failed": failed,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    cover_brief = "，".join(
        f"{DIRECTION_LABEL[d]} {made_by_dir[d]}" for d in DIRECTION_ORDER if made_by_dir[d] > 0
    )
    log(f"\n全部完成：成功 {len(made)}/{target}（方向覆盖：{cover_brief or '无'}）")
    for item in made:
        tag = DIRECTION_LABEL.get(item.get("direction", ""), "?")
        log(f"  ✓ [{tag}] {item.get('title')} → {item.get('video')}")
    if failed:
        log(f"\n失败 {len(failed)} 条：")
        for item in failed:
            log(f"  ✗ [{item.get('score')}] {item.get('title')} → {item.get('error')}")
    log("\n" + cost_tracker.report_window(run_start, videos=len(made)))
    return 0 if len(made) >= target else 1


if __name__ == "__main__":
    raise SystemExit(main())
