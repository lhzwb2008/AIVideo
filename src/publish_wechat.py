#!/usr/bin/env python3
"""发布论坛图文包到微信公众号（官方 API）。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from paths import ROOT
from research import load_env
from wechat_publisher import WechatPublishError, publish_forum_pack


def resolve_pack(path: str | None) -> Path:
    if path:
        pack = Path(path)
        if not pack.is_absolute():
            pack = ROOT / pack
        if not pack.is_dir():
            raise WechatPublishError(f"论坛包目录不存在: {pack}")
        return pack.resolve()

    published = ROOT / "archive" / "published"
    candidates = sorted(
        published.glob("*/*/post.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise WechatPublishError("未找到论坛包，请指定目录")
    return candidates[0].parent.resolve()


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="发布图文到微信公众号")
    parser.add_argument(
        "pack",
        nargs="?",
        help="论坛包目录（含 post.md + cover.jpg + images/）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只解析，不调用 API")
    parser.add_argument(
        "--draft-only",
        action="store_true",
        help="只存草稿箱，不提交发布",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="草稿后提交发布（覆盖 WECHAT_DRAFT_ONLY=1）",
    )
    parser.add_argument(
        "--clear-drafts",
        action="store_true",
        help="发布前先清空草稿箱（API draft/delete）",
    )
    args = parser.parse_args()

    try:
        pack = resolve_pack(args.pack)
        draft_only = None
        if args.draft_only:
            draft_only = True
        elif args.publish:
            draft_only = False

        if args.clear_drafts:
            from wechat_publisher import clear_all_drafts

            n = clear_all_drafts()
            if n:
                print(f"已清空草稿箱 {n} 篇", flush=True)

        if args.dry_run:
            preview = publish_forum_pack(pack, dry_run=True)
            print(
                f"[dry-run] 微信公众号: {preview['title']}"
                f"（{preview['sections']} 段）",
                flush=True,
            )
            return 0

        print(f"发布微信公众号图文：{pack}", flush=True)
        result = publish_forum_pack(pack, draft_only=draft_only)
        if result.get("published"):
            note = result.get("publish_note") or ""
            if note:
                print(f"  已发表（{note}）", flush=True)
            else:
                print("  已提交发布", flush=True)
        elif result.get("draft_only"):
            print(f"  草稿已保存 media_id={result['draft_media_id']}", flush=True)
            note = result.get("publish_note") or ""
            if note:
                print(f"  （{note}）", flush=True)
            else:
                print("  请在 mp.weixin.qq.com 草稿箱查看并手动发布", flush=True)
        else:
            print(
                f"  已提交发布任务 publish_id={result['publish_id']}"
                "（状态轮询未确认成功，请到后台查看）",
                flush=True,
            )

        log_path = ROOT / "logs" / "last_wechat_publish.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            **result,
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
        log_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"  记录: {log_path}", flush=True)
        return 0
    except WechatPublishError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
