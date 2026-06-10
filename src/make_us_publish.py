#!/usr/bin/env python3
"""US Market 英文热点：Cursor 联网写稿 → 改编 → 生图合成 → YouTube/TikTok。"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone

import cost_tracker
from paths import ROOT
from publish_pipeline import log, pipeline_after_script
from publish_caption import tiktok_enabled
from locale_env import load_locale_env, locale_logs_dir
from research import load_env
from us_cursor_topics import (
    SLOT_LABEL,
    US_SLOT_ORDER,
    build_us_cursor_research,
    discover_us_cursor_topics,
)
from us_market import adapt_us_script, append_us_history
from us_voice import apply_voice_env, list_voices, resolve_voice


_US_OUTRO_VARIANTS = (
    "That's the sketch for today. Save it if useful — and tell me what you'd watch next.",
    "Quick market sketch. Save this for later and follow for the next one.",
    "That's today's sketch. Bookmark it if it helped — follow for daily updates.",
    "Market Sketch signing off. Save this episode and follow for the next move.",
    "That's the wrap. If this clarified anything, save it — tell us what to cover next.",
    "Plain-English markets, one sketch at a time. Save, follow, see you tomorrow.",
)


def apply_us_publish_env(*, voice_id: str | None = None) -> str:
    """设置英文流水线默认环境：仅 YT/TikTok，Cursor 写稿。"""
    os.environ["AIVIDEO_LOCALE"] = "en"
    os.environ["AIVIDEO_SOURCE"] = "cursor"
    os.environ["AIVIDEO_FORUM_POST"] = "0"
    os.environ["AIVIDEO_PUBLISH_YOUTUBE"] = "1"
    os.environ["AIVIDEO_PUBLISH_TIKTOK"] = "1"
    os.environ["AIVIDEO_PUBLISH_INSTAGRAM"] = "1"
    os.environ["AIVIDEO_PUBLISH_FACEBOOK"] = "1"
    os.environ["AIVIDEO_PUBLISH_LINKEDIN"] = "1"
    for key in (
        "AIVIDEO_PUBLISH_BILIBILI",
        "AIVIDEO_PUBLISH_DOUYIN",
        "AIVIDEO_PUBLISH_EASTMONEY",
        "AIVIDEO_PUBLISH_XUEQIU",
        "AIVIDEO_PUBLISH_WECHAT",
        "AIVIDEO_PUBLISH_ZHIHU",
        "AIVIDEO_PUBLISH_XHS",
        "AIVIDEO_PUBLISH_KS",
        "AIVIDEO_PUBLISH_SHIPINHAO",
    ):
        os.environ[key] = "0"
    # 强制覆盖 .env 里的中文品牌/尾页（setdefault 会被 .env 抢先写入）
    os.environ["AIVIDEO_BRAND_NAME"] = "Market Sketch"
    os.environ["AIVIDEO_BRAND_TAGLINE"] = "US markets in plain English"
    os.environ["AIVIDEO_OUTRO_HEADLINE"] = "Like · Save · Follow"
    os.environ["AIVIDEO_OUTRO_SUBLINE"] = "US markets in plain English"
    os.environ["AIVIDEO_OUTRO_NARRATION"] = _US_OUTRO_VARIANTS[0]
    os.environ["AIVIDEO_OUTRO_NARRATION_VARIANTS"] = "|".join(_US_OUTRO_VARIANTS)
    os.environ.setdefault("YOUTUBE_HASHTAGS", "#stocks #USmarket #finance #investing #Shorts")
    os.environ.setdefault("TIKTOK_HASHTAGS", "#stocks #finance #investing #wallstreet #money")
    os.environ.setdefault("YOUTUBE_DISCLAIMER", "For education only. Not investment advice.")
    os.environ.setdefault("TIKTOK_DISCLAIMER", "For education only. Not investment advice.")
    vid = apply_voice_env(voice_id)
    # 再次强制写入，防止 load_env / 子 shell source .env 用克隆音色覆盖
    apply_voice_env(vid)
    return vid


def main() -> int:
    load_locale_env("en")
    default_count = len(US_SLOT_ORDER)
    parser = argparse.ArgumentParser(
        description="US Market：Cursor 写稿 → 英文短视频 → YouTube/TikTok"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=int(os.environ.get("AIVIDEO_MAX_VIDEOS_PER_RUN", str(default_count))),
        help=f"本次制作条数（默认 {default_count}，三槽位各 1）",
    )
    parser.add_argument("--topic", help="手动指定英文话题（仅做 1 条）")
    parser.add_argument("--voice", help="音色 ID，见 assets/us-voices.json")
    parser.add_argument("--list-voices", action="store_true", help="列出可选英文男声")
    parser.add_argument("--dry-run", action="store_true", help="预演发布参数")
    parser.add_argument("--no-publish", action="store_true", help="只生成视频")
    args = parser.parse_args()

    if args.list_voices:
        for row in list_voices():
            print(f"  {row['id']:10}  {row.get('name', '')} — {row.get('desc', '')}", flush=True)
        print("\n试听: ./scripts/preview-us-voices.sh", flush=True)
        return 0

    voice = apply_us_publish_env(voice_id=args.voice)
    _, voice_cfg = resolve_voice(voice)
    target = 1 if args.topic else max(1, min(args.count, len(US_SLOT_ORDER)))
    topics = discover_us_cursor_topics(target=target, custom_hint=args.topic or "")

    log(f"US Market 模式 | locale=en | voice={voice} ({voice_cfg.get('name', '')})")
    log("调研: Cursor Cloud Agent 联网写稿 | 改编: Opus 英文脚本")
    log("发布: YouTube + TikTok + Instagram + Facebook + LinkedIn")
    try:
        from tiktok_auth import tiktok_direct_post_ready

        tk_ready, tk_reason = tiktok_direct_post_ready()
        if tiktok_enabled() and not tk_ready:
            log(f"TikTok: 跳过自动发布（{tk_reason}）")
    except Exception:
        pass
    log(
        f"本次 {target} 条；槽位："
        + " → ".join(SLOT_LABEL.get(t["slot"], t["slot"]) for t in topics)
    )

    run_start = time.time()
    made: list[dict] = []
    failed: list[dict] = []
    agent_id: str | None = None
    reuse = os.environ.get("AIVIDEO_CURSOR_REUSE_AGENT", "1").strip() not in ("0", "false", "no")

    for index, topic in enumerate(topics, 1):
        slot = topic.get("slot", "us_wrap")
        log(f"\n>>> #{index}/{target} [{SLOT_LABEL.get(slot, slot)}] {topic.get('title_hint', '')}")
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            script_path = locale_logs_dir("en") / f"last_script_{stamp}_us{index:02d}.json"

            article, details, agent_id = build_us_cursor_research(
                topic,
                agent_id=agent_id if reuse else None,
            )
            if not reuse:
                agent_id = None

            log(f"  ✓ 深读: {len(details.get('outline') or [])} sections")
            script = adapt_us_script(article, details=details)
            script_path.write_text(
                json.dumps(
                    {
                        "mode": "us_market_cursor",
                        "locale": "en",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "topic": topic,
                        "article": article,
                        "research_details": details,
                        "script": script,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            title = str(script.get("title") or "").strip()
            log(f"脚本标题: {title}")

            def _append(_path, _script=script, _slot=slot, _topic=topic):  # noqa: ANN001
                append_us_history(
                    {**_script, "slot": _slot, "theme_cluster": _topic.get("theme_cluster")},
                    video="",
                )

            result = pipeline_after_script(
                script_path,
                title,
                index=index,
                target=target,
                publish_check=False,
                dry_run=args.dry_run,
                skip_publish=args.no_publish,
                append_history_fn=_append,
            )
            append_us_history(
                {**script, "slot": slot, "theme_cluster": topic.get("theme_cluster")},
                video=str(result.get("video") or ""),
            )
            result["slot"] = slot
            result["title_hint"] = topic.get("title_hint")
            made.append(result)
        except Exception as exc:  # noqa: BLE001
            log(f"\n✗ 失败 [{SLOT_LABEL.get(slot, slot)}]: {exc}")
            failed.append({"slot": slot, "title": topic.get("title_hint"), "error": str(exc)})
            if reuse:
                agent_id = None

    summary = locale_logs_dir("en") / "make_us_publish_last.json"
    summary.write_text(
        json.dumps(
            {
                "mode": "us_market_cursor",
                "locale": "en",
                "voice": voice,
                "target": target,
                "topics": topics,
                "made": made,
                "failed": failed,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    log(f"\n全部完成：成功 {len(made)}/{target}")
    for item in made:
        tag = SLOT_LABEL.get(item.get("slot", ""), "?")
        log(f"  ✓ [{tag}] {item.get('title')} → {item.get('video')}")
    if failed:
        log(f"\n失败 {len(failed)} 条：")
        for item in failed:
            log(f"  ✗ {SLOT_LABEL.get(item.get('slot'), '?')} → {item.get('error')}")
    log("\n" + cost_tracker.report_window(run_start, videos=len(made)))
    return 0 if len(made) >= target and not failed else (1 if failed else 0)


if __name__ == "__main__":
    raise SystemExit(main())
