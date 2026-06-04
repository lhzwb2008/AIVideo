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
    DRAFT_HINT,
    XhsArticlePublishError,
    clear_forum_draft,
    cookie_path,
    open_creator_browser,
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
    force: bool = False,
) -> dict:
    def attempt() -> dict:
        return asyncio.run(
            publish_forum_pack(
                pack,
                headless=headless,
                account=account,
                script=script,
                force=force,
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


def clear_pack(
    pack: Path,
    *,
    headless: bool,
    account: str,
    script: dict | None = None,
    interactive_login: bool = True,
) -> dict:
    def attempt() -> dict:
        return asyncio.run(
            clear_forum_draft(
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
    force: bool = False,
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
        force=force,
    )
    _write_log(result)
    if result.get("skipped"):
        print(f"  ↳ {result.get('message', '已跳过')}", flush=True)
    return str(result.get("title") or "")


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="小红书图文笔记 · 保存草稿")
    parser.add_argument("pack_dir", nargs="?", help="论坛包目录")
    parser.add_argument(
        "--headed",
        action="store_true",
        help="有头浏览器（填表失败时建议开启）",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--open-creator",
        action="store_true",
        help="用与脚本相同的浏览器配置打开创作中心（查看本地草稿）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="草稿箱无同标题时新建；已有则点「编辑」覆盖更新（不另起一篇）",
    )
    parser.add_argument("--clear-draft", action="store_true", help="删除同标题图文草稿后退出")
    parser.add_argument("--account", default=os.environ.get("SAU_XHS_ACCOUNT", "main"))
    args = parser.parse_args()

    if args.open_creator:
        try:
            asyncio.run(open_creator_browser(account=args.account))
        except XhsArticlePublishError as exc:
            print(f"打开失败: {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        pack = resolve_pack(args.pack_dir)
    except XhsArticlePublishError as exc:
        print(f"发布失败: {exc}", file=sys.stderr)
        return 1

    try:
        if args.clear_draft:
            result = clear_pack(
                pack,
                headless=not args.headed,
                account=args.account,
            )
            print(f"已清理小红书同标题草稿 {result.get('deleted', 0)} 篇：{result['title']}")
            return 0
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
            force=args.force,
        )
        log_path = _write_log(result)
        if result.get("skipped"):
            print(result.get("message") or "已跳过：草稿已存在")
            print(f"标题: {result['title']}")
            print(result.get("draft_hint") or DRAFT_HINT)
            print(f"记录: {log_path}")
            return 0
        print(f"草稿已保存：{result['title']}")
        count = result.get("image_draft_count")
        if count is not None:
            print(f"草稿箱图文笔记数: {count}")
        print(result.get("draft_hint") or DRAFT_HINT)
        print(f"浏览器配置: {result.get('browser_profile', '')}")
        print(f"记录: {log_path}")
        return 0
    except XhsArticlePublishError as exc:
        print(f"发布失败: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
