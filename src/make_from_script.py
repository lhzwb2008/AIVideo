#!/usr/bin/env python3
"""直接喂文案模式：跳过找文章/调研/改编，直接拿现成文案 → 生图 → 合成 → 发布。

适用场景：
  针对某些特殊话题/场景，你（或模型）已经按"生图要求"亲自写好了分页文案，
  不想再走 make-topics 的「搜文章 → 深读 → 改编」链路，只想把文案直接做成视频。

输入是一个 JSON 文件（或 stdin），描述一条（或多条）视频脚本：

  {
    "title": "换手率高到底是好事还是坏事？",   # 4-30 字
    "keyword": "换手率",                       # 可选，缺省自动取
    "hashtags": ["换手率", "A股", "炒股入门"], # 可选，最多 5 个
    "category": "basic",                       # 可选：子栏目 id，缺省自动判定
    "slides": [
      {
        "headline": "换手率到底是啥",          # ≤14 字
        "narration": "……口播文案……",          # cover 页 40-120 字；正文页 50-220 字
        "image_prompt": "a theater with 1000 seats, ...",  # 英文画面描述
        "on_image_text": ["换手率=今天换了多少手", "剧院1000座位"],  # 3-12 条，每条 ≤16 字
        "chapter_title": "换手率",             # 可选 2-6 字，缺省由 headline 推导
        "subtitle": "换手率到底是啥",          # 仅第 1 页（cover）需要，6-24 字，缺省自动取
        "lead_in": "高低怎么看"                # 仅正文页需要，≤14 字，缺省自动取
      },
      ...
    ]
  }

  · 第 1 页自动当作封面（cover），其余为正文（body）。
  · 顶层若是数组 [ {...}, {...} ]，则视为多条脚本，逐条制作发布。
  · 各字段长度/合规会用与 make-topics 完全相同的规则校验（research.validate_article_script），
    不通过会直接报错并指出哪一页哪个字段不合规，便于你按提示修文案。

用法：
  python3 src/make_from_script.py script.json                 # 制作并发布
  python3 src/make_from_script.py script.json --no-publish     # 只生成不发布
  python3 src/make_from_script.py script.json --check          # 发布前检查抖音登录态
  cat script.json | python3 src/make_from_script.py -          # 从 stdin 读
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import categories
import cost_tracker
import research
from batch_aivideo import append_history_from_script
from publish_pipeline import log, pipeline_after_script, read_script_title
from paths import ROOT
from research import load_env


def read_payload(args: argparse.Namespace) -> object:
    if args.file == "-" or (not args.file and not sys.stdin.isatty()):
        raw = sys.stdin.read()
    else:
        path = Path(args.file)
        if not path.is_file():
            raise SystemExit(f"找不到文案文件：{path}")
        raw = path.read_text(encoding="utf-8")
    raw = raw.strip()
    if not raw:
        raise SystemExit("文案输入为空。")
    return json.loads(raw)


def _article_stub(payload: dict) -> dict:
    """构造一个无来源的合成 article（与指定话题自带内容模式同形）。"""
    title = str(payload.get("title") or "").strip()
    return {
        "title": title,
        "question_title": "",
        "url": "",
        "site": "",
        "author": "",
        "published_at": "",
        "language": "zh",
        "summary_zh": title,
        "thesis": title,
        "key_facts": [title],
        "narrative_arc": "自带文案",
        "source_type": "scripted",
        "_no_source": True,
    }


def build_script(payload: dict) -> tuple[dict, dict]:
    """把用户提供的文案 payload 规范化、校验为合成管线需要的完整 script。

    复用 research 里与 make-topics 相同的归一化/校验逻辑，保证两条链路一致。
    返回 (script, article)。
    """
    if not isinstance(payload, dict):
        raise ValueError("每条脚本必须是 JSON 对象（含 title 与 slides）。")
    if not isinstance(payload.get("slides"), list) or not payload["slides"]:
        raise ValueError("缺少 slides（至少 3 页）。")

    article = _article_stub(payload)
    explicit_category = payload.get("category")

    # 只取脚本相关字段，避免把多余键带进 script。
    data = {
        "title": str(payload.get("title") or "").strip(),
        "keyword": payload.get("keyword"),
        "hashtags": payload.get("hashtags") or [],
        "slides": payload["slides"],
        "source": payload.get("source"),
    }
    data = research.merge_article_into_script(data, article)
    data = research.soft_sanitize_script(data)
    script = research.validate_article_script(data, article)
    research.print_douyin_pre_publish_scan(script)

    try:
        resolved = categories.resolve_category(script, explicit_category)
        if resolved:
            script["category"] = resolved
            log(f"  🏷  子栏目：{categories.label_of(resolved)}（{resolved}）")
    except Exception as exc:  # noqa: BLE001
        log(f"  ⚠️  子栏目判定失败，用默认主题：{exc}")

    return script, article


def write_script_file(script: dict, article: dict, index: int) -> Path:
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    script_path = logs_dir / f"last_script_{stamp}_scripted{index:02d}.json"
    meta = {
        "mode": "scripted",
        "days": 0,
        "agent_id": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "article": article,
        "script": script,
    }
    script_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return script_path


def process_one(
    index: int,
    *,
    target: int,
    payload: dict,
    publish_check: bool,
    dry_run: bool,
    skip_publish: bool,
) -> dict:
    title_hint = str(payload.get("title") or f"脚本{index}").strip()
    log(f"\n=== [{index}/{target}] 文案：{title_hint} ===")
    script, article = build_script(payload)
    script_path = write_script_file(script, article, index)
    title = str(script.get("title") or read_script_title(script_path) or "").strip()
    log(f"脚本标题：{title}  （{len(script.get('slides') or [])} 页）")

    return pipeline_after_script(
        script_path,
        title,
        index=index,
        target=target,
        publish_check=publish_check,
        dry_run=dry_run,
        skip_publish=skip_publish,
        append_history_fn=append_history_from_script,
    )


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="AI财知道：直接喂文案一键生图/合成/发布")
    parser.add_argument("file", nargs="?", help="文案 JSON 文件；用 - 从 stdin 读")
    parser.add_argument("--check", action="store_true", help="（已废弃，保留兼容）")
    parser.add_argument("--dry-run", action="store_true", help="只预演发布参数，不真正发布/归档")
    parser.add_argument("--no-publish", action="store_true", help="只生成视频，跳过发布步骤")
    args = parser.parse_args()

    payload = read_payload(args)
    scripts = payload if isinstance(payload, list) else [payload]
    target = len(scripts)
    log(f"读入 {target} 条文案。")

    run_start = time.time()
    made: list[dict] = []
    failed: list[dict] = []
    for index, item in enumerate(scripts, 1):
        title_hint = str((item or {}).get("title") if isinstance(item, dict) else "") or f"脚本{index}"
        try:
            made.append(
                process_one(
                    index,
                    target=target,
                    payload=item,
                    publish_check=args.check,
                    dry_run=args.dry_run,
                    skip_publish=args.no_publish,
                )
            )
        except Exception as exc:  # noqa: BLE001
            log(f"\n✗ 文案失败：{title_hint}：{exc}")
            failed.append({"title": title_hint, "error": str(exc)})
            continue

    summary = ROOT / "logs" / "make_from_script_last.json"
    summary.write_text(
        json.dumps(
            {
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
        log(f"  ✓ {item.get('title')} → {item.get('video')}")
    if failed:
        log(f"\n失败 {len(failed)} 条：")
        for item in failed:
            log(f"  ✗ {item.get('title')} → {item.get('error')}")
    log("\n" + cost_tracker.report_window(run_start, videos=len(made)))
    return 0 if made else 1


if __name__ == "__main__":
    raise SystemExit(main())
