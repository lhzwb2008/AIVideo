#!/usr/bin/env python3
"""发布论坛图文包到小红书图文笔记草稿箱。"""

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
from xhs_article_publisher import (
    XhsArticlePublishError,
    cookie_path,
    parse_forum_pack,
    publish_forum_pack,
)


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
            raise XhsArticlePublishError(f"论坛包目录不存在: {pack}")
        return pack

    published = ROOT / "archive" / "published"
    candidates = sorted(
        published.glob("*/*/post.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise XhsArticlePublishError("未找到论坛包，请指定目录")
    return candidates[0].parent


def _write_log(result: dict) -> Path:
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "last_xhs_article_publish.json"
    payload = {"at": datetime.now(timezone.utc).isoformat(), **result}
    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    history = log_dir / "xhs_article_publish_history.jsonl"
    with history.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return log_path


def _cookie_ok(account: str) -> bool:
    try:
        path = cookie_path(account=account)
        return path.is_file() and path.stat().st_size > 64
    except XhsArticlePublishError:
        return False


def publish_pack(
    pack: Path,
    *,
    headless: bool,
    account: str,
    script: dict | None = None,
    interactive_login: bool = True,
) -> dict:
    def attempt() -> dict:
        return asyncio.run(
            publish_forum_pack(
                pack,
                headless=headless,
                account=account,
                script=script,
            )
        )

    if not interactive_login:
        return attempt()
    return run_with_relogin(
        attempt,
        platform="xiaohongshu",
        account=account,
        label="小红书图文",
        interactive_login=True,
    )


def publish_forum_dir(
    forum_dir: Path | str,
    *,
    dry_run: bool = False,
    account: str | None = None,
    script: dict | None = None,
) -> str:
    load_env()
    account = account or os.environ.get("SAU_XHS_ACCOUNT", "main")
    pack = Path(forum_dir)
    if not pack.is_absolute():
        pack = ROOT / pack
    if not (pack / "post.md").is_file():
        return ""

    print(f"\n[发布小红书图文草稿] {pack}", flush=True)
    if dry_run:
        if not _cookie_ok(account):
            raise XhsArticlePublishError("未登录")
        data = parse_forum_pack(pack)
        return data["title"]

    result = publish_pack(
        pack,
        headless=True,
        account=account,
        script=script,
        interactive_login=True,
    )
    _write_log(result)
    return str(result.get("title") or "")


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="小红书图文笔记 · 保存草稿")
    parser.add_argument("pack_dir", nargs="?", help="论坛包目录")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--account", default=os.environ.get("SAU_XHS_ACCOUNT", "main"))
    args = parser.parse_args()

    try:
        pack = resolve_pack(args.pack_dir)
    except XhsArticlePublishError as exc:
        print(f"发布失败: {exc}", file=sys.stderr)
        return 1

    try:
        if args.dry_run:
            if not _cookie_ok(args.account):
                raise XhsArticlePublishError("未登录")
            data = parse_forum_pack(pack)
            print(f"预演通过：{data['title']}")
            return 0
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
    except XhsArticlePublishError as exc:
        print(f"发布失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
