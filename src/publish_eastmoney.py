#!/usr/bin/env python3
"""发布论坛图文包到东方财富创作平台。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from eastmoney_publisher import EastmoneyPublishError, publish_forum_pack
from paths import ROOT


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def resolve_pack(path: str | None) -> Path:
    if path:
        pack = Path(path)
        if not pack.is_absolute():
            pack = ROOT / pack
        if not pack.is_dir():
            raise EastmoneyPublishError(f"论坛包目录不存在: {pack}")
        return pack

    published = ROOT / "archive" / "published"
    candidates = sorted(
        published.glob("*/*/post.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise EastmoneyPublishError("未找到论坛包，请指定目录")
    return candidates[0].parent


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="东方财富长文图文发布")
    parser.add_argument(
        "pack_dir",
        nargs="?",
        help="论坛包目录（含 post.md cover.jpg images/）",
    )
    parser.add_argument("--headed", action="store_true", help="有头浏览器（调试）")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="直接点发布（默认仅填表+保存预览）",
    )
    parser.add_argument("--account", default=os.environ.get("EASTMONEY_ACCOUNT", "main"))
    args = parser.parse_args()

    try:
        pack = resolve_pack(args.pack_dir)
        result = asyncio.run(
            publish_forum_pack(
                pack,
                headless=not args.headed,
                draft_only=not args.publish,
                account=args.account,
            )
        )
    except EastmoneyPublishError as exc:
        print(f"发布失败: {exc}", file=sys.stderr)
        return 1

    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "last_eastmoney_publish.json"
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        **result,
    }
    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    mode = "已提交发布" if args.publish else "已填表（含封面+配图）"
    print(f"{mode}：{result['title']}")
    print(f"素材目录: {result['pack_dir']}")
    print(f"封面: {result['cover']}")
    print(f"配图: {len(result.get('images') or [])} 张")
    print(f"记录: {log_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
