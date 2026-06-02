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

from eastmoney_publisher import EastmoneyPublishError, parse_forum_pack, publish_forum_pack
from forum_auth import run_with_relogin
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


def packs_for_date(date_tag: str) -> list[Path]:
    base = ROOT / "archive" / "published" / date_tag
    if not base.is_dir():
        raise EastmoneyPublishError(f"归档日期目录不存在: {base}")
    packs = sorted(
        {p.parent for p in base.glob("*/post.md")},
        key=lambda p: p.name,
    )
    if not packs:
        raise EastmoneyPublishError(f"{date_tag} 下无论坛包")
    return packs


def _write_log(result: dict) -> Path:
    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "last_eastmoney_publish.json"
    payload = {"at": datetime.now(timezone.utc).isoformat(), **result}
    log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    history = log_dir / "eastmoney_publish_history.jsonl"
    with history.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return log_path


def _dry_run_check(pack: Path, *, account: str) -> dict:
    data = parse_forum_pack(pack)
    from eastmoney_session import verify_editor_sync

    if not verify_editor_sync(account=account):
        raise EastmoneyPublishError("未登录")
    return {
        "title": data["title"],
        "pack_dir": data["pack_dir"],
        "cover": data["cover"],
        "images": [s.get("image") for s in data["sections"] if s.get("image")],
        "dry_run": True,
    }


def publish_pack(
    pack: Path,
    *,
    headless: bool,
    publish: bool,
    account: str,
    interactive_login: bool = True,
) -> dict:
    def attempt() -> dict:
        if publish:
            return asyncio.run(
                publish_forum_pack(
                    pack,
                    headless=headless,
                    draft_only=False,
                    account=account,
                )
            )
        return _dry_run_check(pack, account=account)

    if not interactive_login:
        return attempt()
    return run_with_relogin(
        attempt,
        platform="eastmoney",
        account=account,
        label="东方财富",
        interactive_login=True,
    )


def _default_headless() -> bool:
    return os.environ.get("EASTMONEY_HEADLESS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def publish_forum_dir(
    forum_dir: Path | str,
    *,
    dry_run: bool = False,
    account: str | None = None,
) -> str:
    """主流程入口：发布单个论坛包，cookie 失效时等待扫码后继续。"""
    load_env()
    account = account or os.environ.get("EASTMONEY_ACCOUNT", "main")
    pack = Path(forum_dir)
    if not pack.is_absolute():
        pack = ROOT / pack
    if not (pack / "post.md").is_file():
        return ""

    print(f"\n[发布东方财富] {pack}", flush=True)
    result = publish_pack(
        pack,
        headless=_default_headless(),
        publish=not dry_run,
        account=account,
        interactive_login=True,
    )
    if result and not dry_run:
        _write_log({**result, "draft_only": False})
    return str(result.get("title") or "")


def _publish_one(
    pack: Path,
    *,
    headless: bool,
    publish: bool,
    account: str,
    interactive_login: bool = True,
) -> dict:
    return publish_pack(
        pack,
        headless=headless,
        publish=publish,
        account=account,
        interactive_login=interactive_login,
    )


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description="东方财富长文图文发布")
    parser.add_argument(
        "pack_dir",
        nargs="?",
        help="论坛包目录（含 post.md cover.jpg images/）",
    )
    parser.add_argument(
        "--date",
        metavar="YYYYMMDD",
        help="发布该日 archive/published 下全部论坛包",
    )
    parser.add_argument("--headed", action="store_true", help="有头浏览器（调试）")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="直接点发布（默认仅校验/预览）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="校验登录态与素材，不打开浏览器填表",
    )
    parser.add_argument("--account", default=os.environ.get("EASTMONEY_ACCOUNT", "main"))
    args = parser.parse_args()

    try:
        if args.date:
            packs = packs_for_date(args.date)
        else:
            packs = [resolve_pack(args.pack_dir)]
    except EastmoneyPublishError as exc:
        print(f"发布失败: {exc}", file=sys.stderr)
        return 1

    publish = args.publish and not args.dry_run
    ok = 0
    for pack in packs:
        try:
            if args.dry_run:
                result = publish_pack(
                    pack,
                    headless=not args.headed,
                    publish=False,
                    account=args.account,
                    interactive_login=True,
                )
            else:
                result = _publish_one(
                    pack,
                    headless=not args.headed,
                    publish=publish,
                    account=args.account,
                )
        except EastmoneyPublishError as exc:
            print(f"发布失败 [{pack.name}]: {exc}", file=sys.stderr)
            continue
        log_path = _write_log({**result, "draft_only": not publish})
        mode = "已提交发布" if publish else ("预演通过" if args.dry_run else "已填表（含封面+配图）")
        print(f"{mode}：{result['title']}")
        print(f"素材目录: {result['pack_dir']}")
        print(f"封面: {result['cover']}")
        print(f"配图: {len(result.get('images') or [])} 张")
        print(f"记录: {log_path}")
        ok += 1

    if ok == 0:
        return 1
    if len(packs) > 1:
        print(f"\n共 {ok}/{len(packs)} 篇完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
