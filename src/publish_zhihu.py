#!/usr/bin/env python3
"""发布论坛图文包到知乎专栏草稿箱。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from forum_auth import run_with_relogin
from paths import ROOT
from zhihu_publisher import ZhihuPublishError, parse_forum_pack, publish_forum_pack


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
            raise ZhihuPublishError(f"论坛包目录不存在: {pack}")
        return pack

    published = ROOT / "archive" / "published"
    candidates = sorted(
        published.glob("*/*/post.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise ZhihuPublishError("未找到论坛包，请指定目录")
    return candidates[0].parent


def _write_log(result: dict) -> Path:
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "last_zhihu_publish.json"
    payload = {"at": datetime.now(timezone.utc).isoformat(), **result}
    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    history = log_dir / "zhihu_publish_history.jsonl"
    with history.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return log_path


def _dry_run_check(pack: Path, *, account: str) -> dict:
    from zhihu_session import verify_editor_sync

    data = parse_forum_pack(pack)
    if not verify_editor_sync(account=account):
        raise ZhihuPublishError("未登录")
    return {
        "title": data["title"],
        "pack_dir": data["pack_dir"],
        "dry_run": True,
    }


def publish_pack(
    pack: Path,
    *,
    headless: bool,
    account: str,
    interactive_login: bool = True,
) -> dict:
    def attempt() -> dict:
        return asyncio.run(
            publish_forum_pack(pack, headless=headless, account=account)
        )

    if not interactive_login:
        return attempt()
    return run_with_relogin(
        attempt,
        platform="zhihu",
        account=account,
        label="知乎专栏",
        interactive_login=True,
    )


def publish_forum_dir(
    forum_dir: Path | str,
    *,
    dry_run: bool = False,
    account: str | None = None,
) -> str:
    load_env()
    account = account or os.environ.get("ZHIHU_ACCOUNT", "main")
    pack = Path(forum_dir)
    if not pack.is_absolute():
        pack = ROOT / pack
    if not (pack / "post.md").is_file():
        return ""

    print(f"\n[发布知乎专栏草稿] {pack}", flush=True)
    if dry_run:
        result = _dry_run_check(pack, account=account)
    else:
        result = publish_pack(pack, headless=True, account=account)
        _write_log(result)
    return str(result.get("title") or "")


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="知乎专栏 · 保存草稿（不发布）")
    parser.add_argument("pack_dir", nargs="?", help="论坛包目录")
    parser.add_argument("--headed", action="store_true", help="有头浏览器")
    parser.add_argument("--dry-run", action="store_true", help="仅校验登录与素材")
    parser.add_argument("--account", default=os.environ.get("ZHIHU_ACCOUNT", "main"))
    args = parser.parse_args()

    try:
        pack = resolve_pack(args.pack_dir)
    except ZhihuPublishError as exc:
        print(f"发布失败: {exc}", file=sys.stderr)
        return 1

    try:
        if args.dry_run:
            result = _dry_run_check(pack, account=args.account)
        else:
            result = publish_pack(
                pack,
                headless=not args.headed,
                account=args.account,
            )
            log_path = _write_log(result)
            print(f"草稿已保存：{result['title']}")
            print(f"草稿箱: {result.get('url')}")
            print(f"记录: {log_path}")
            return 0
    except ZhihuPublishError as exc:
        print(f"发布失败: {exc}", file=sys.stderr)
        return 1

    print(f"预演通过：{result['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
