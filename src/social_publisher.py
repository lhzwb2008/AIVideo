#!/usr/bin/env python3
"""统一的多平台发布器：小红书 / 快手 / 视频号（复用 vendor 的 social-auto-upload）。

本脚本需在 social-auto-upload 的 venv 解释器下运行（由 scripts/publish-social.sh
和 social-login.sh 负责选解释器并注入 PYTHONPATH），因为它直接 import 该项目的
uploader 模块与 conf。

子命令：
  login    扫码登录 / 续期 cookie（有头）
  check    校验 cookie 是否有效
  publish  发布单条视频

平台 key：xiaohongshu | kuaishou | tencent(视频号)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path


PLATFORM_ALIASES = {
    "xiaohongshu": "xiaohongshu",
    "xhs": "xiaohongshu",
    "redbook": "xiaohongshu",
    "kuaishou": "kuaishou",
    "ks": "kuaishou",
    "tencent": "tencent",
    "shipinhao": "tencent",
    "weixin": "tencent",
    "channels": "tencent",
}

PLATFORM_LABEL = {
    "xiaohongshu": "小红书",
    "kuaishou": "快手",
    "tencent": "视频号",
}


def _norm_platform(value: str) -> str:
    key = PLATFORM_ALIASES.get(value.strip().lower())
    if not key:
        raise SystemExit(f"未知平台: {value}（可选: xiaohongshu/kuaishou/shipinhao）")
    return key


def _sau_home() -> Path:
    custom = os.environ.get("SAU_HOME", "").strip()
    if custom:
        return Path(custom).expanduser()
    # src 同级的项目根 → vendor/social-auto-upload
    return Path(__file__).resolve().parents[1] / "vendor" / "social-auto-upload"


def _account(platform: str) -> str:
    env_key = {
        "xiaohongshu": "SAU_XHS_ACCOUNT",
        "kuaishou": "SAU_KUAISHOU_ACCOUNT",
        "tencent": "SAU_SHIPINHAO_ACCOUNT",
    }[platform]
    return os.environ.get(env_key, "").strip() or "main"


def _account_file(platform: str, account: str) -> Path:
    path = _sau_home() / "cookies" / f"{platform}_{account}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_script(path: Path | None) -> dict | None:
    if not path or not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("script", data)


def _resolve_cover(script_path: Path | None) -> Path | None:
    root = Path(__file__).resolve().parents[1]
    if not script_path or not script_path.is_file():
        return None
    cover = root / "logs" / "images" / script_path.stem / "cover.png"
    return cover if cover.is_file() else None


# ---------------------------------------------------------------------------
# 平台适配：返回 (uploader_class, setup_coro_factory, immediate_strategy)
# ---------------------------------------------------------------------------
def _load_platform(platform: str):
    if platform == "xiaohongshu":
        from uploader.xiaohongshu_uploader.main import (
            XIAOHONGSHU_PUBLISH_STRATEGY_IMMEDIATE as IMM,
            XiaoHongShuVideo as Video,
            xiaohongshu_setup as setup,
        )
        return Video, setup, IMM
    if platform == "kuaishou":
        from uploader.ks_uploader.main import (
            KUAISHOU_PUBLISH_STRATEGY_IMMEDIATE as IMM,
            KSVideo as Video,
            ks_setup as setup,
        )
        return Video, setup, IMM
    if platform == "tencent":
        from uploader.tencent_uploader.main import (
            TENCENT_PUBLISH_STRATEGY_IMMEDIATE as IMM,
            TencentVideo as Video,
            tencent_setup as setup,
        )
        return Video, setup, IMM
    raise SystemExit(f"不支持的平台: {platform}")


async def _do_login(platform: str, account_file: Path, headless: bool) -> int:
    _, setup, _ = _load_platform(platform)
    ok = await setup(str(account_file), handle=True, headless=headless)
    if ok:
        print(f"✅ {PLATFORM_LABEL[platform]} 登录态有效: {account_file}", flush=True)
        return 0
    print(f"❌ {PLATFORM_LABEL[platform]} 登录失败/未完成: {account_file}", file=sys.stderr)
    return 1


async def _do_check(platform: str, account_file: Path) -> int:
    _, setup, _ = _load_platform(platform)
    if not account_file.is_file():
        print(f"❌ 未找到 cookie: {account_file}（先运行登录）", file=sys.stderr)
        return 1
    ok = await setup(str(account_file), handle=False)
    if ok:
        print(f"✅ {PLATFORM_LABEL[platform]} cookie 有效", flush=True)
        return 0
    print(f"❌ {PLATFORM_LABEL[platform]} cookie 失效，请重新登录", file=sys.stderr)
    return 1


async def _do_publish(args, platform: str, account_file: Path) -> int:
    Video, setup, immediate = _load_platform(platform)

    if not args.dry_run:
        if not account_file.is_file() or not await setup(str(account_file), handle=False):
            print(
                f"❌ {PLATFORM_LABEL[platform]} cookie 缺失或失效: {account_file}\n"
                f"   请先运行: ./social-login.sh {platform}",
                file=sys.stderr,
            )
            return 1

    video = Path(args.video).resolve()
    if not video.is_file():
        print(f"❌ 视频不存在: {video}", file=sys.stderr)
        return 1

    script_path = Path(args.script).resolve() if args.script else None
    script = _load_script(script_path)

    from social_caption import build_social_fields

    fields = build_social_fields(script, platform)
    title = args.title or fields["title"]
    desc = args.desc if args.desc is not None else fields["desc"]
    tags = (
        [t.strip().lstrip("#") for t in args.tags.split(",") if t.strip()][:5]
        if args.tags is not None
        else fields["tags"][:5]
    )
    thumbnail = Path(args.thumbnail).resolve() if args.thumbnail else _resolve_cover(script_path)

    print(f"平台: {PLATFORM_LABEL[platform]}（{account_file.name}）", flush=True)
    print(f"视频: {video}", flush=True)
    print(f"标题: {title}", flush=True)
    print(f"简介: {desc}", flush=True)
    print(f"标签: {tags}", flush=True)
    if thumbnail:
        print(f"封面: {thumbnail}", flush=True)

    if args.dry_run:
        print("（dry-run，不实际发布）", flush=True)
        return 0

    kwargs = dict(
        title=title,
        file_path=str(video),
        tags=tags,
        publish_date=0,
        account_file=str(account_file),
        desc=desc,
        thumbnail_path=str(thumbnail) if thumbnail else None,
        publish_strategy=immediate,
        headless=not args.headed,
    )
    if platform == "tencent":
        kwargs["short_title"] = fields.get("short_title") or None
        category = os.environ.get("SHIPINHAO_CATEGORY", "").strip()
        if category:
            kwargs["category"] = category

    app = Video(**kwargs)
    print("开始发布（约需 2–5 分钟）…", flush=True)
    await app.main()

    log_path = Path(__file__).resolve().parents[1] / "logs" / f"last_{platform}_publish.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps(
            {
                "platform": platform,
                "account": account_file.name,
                "video": str(video),
                "title": title,
                "desc": desc,
                "tags": tags,
                "published_at": datetime.now().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"✅ 发布已提交，记录: {log_path}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="多平台发布器（小红书/快手/视频号）")
    parser.add_argument("platform", help="xiaohongshu | kuaishou | shipinhao")
    sub = parser.add_subparsers(dest="action", required=True)

    p_login = sub.add_parser("login", help="扫码登录/续期 cookie")
    p_login.add_argument("--headless", action="store_true", help="无头（一般登录需有头扫码）")

    sub.add_parser("check", help="校验 cookie")

    p_pub = sub.add_parser("publish", help="发布单条视频")
    p_pub.add_argument("video", help="MP4 路径")
    p_pub.add_argument("--script", help="脚本 JSON，用于生成标题/简介/标签")
    p_pub.add_argument("--title", help="覆盖标题")
    p_pub.add_argument("--desc", help="覆盖简介")
    p_pub.add_argument("--tags", help="覆盖标签（逗号分隔）")
    p_pub.add_argument("--thumbnail", help="自定义封面图")
    p_pub.add_argument("--headed", action="store_true", help="有头 Chrome（便于调试）")
    p_pub.add_argument("--dry-run", action="store_true", help="只打印参数不发布")

    args = parser.parse_args()
    platform = _norm_platform(args.platform)
    account = _account(platform)
    account_file = _account_file(platform, account)

    if args.action == "login":
        return asyncio.run(_do_login(platform, account_file, headless=args.headless))
    if args.action == "check":
        return asyncio.run(_do_check(platform, account_file))
    if args.action == "publish":
        return asyncio.run(_do_publish(args, platform, account_file))
    parser.error("未知子命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
