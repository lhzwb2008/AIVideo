#!/usr/bin/env python3
"""发布 MP4 到抖音创作者平台（Playwright 自动化）。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lib.douyin_caption import build_sau_fields  # noqa: E402
from lib.douyin_publisher import DouyinPublishError, publish_video, resolve_playwright_python  # noqa: E402
from lib.sau_client import SauError, check_douyin_session, douyin_account  # noqa: E402


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


def load_script(path: Path | None) -> dict | None:
    if not path or not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("script", data)


def resolve_video(path: str | None) -> Path:
    if path:
        video = Path(path)
        if not video.is_file():
            raise DouyinPublishError(f"视频不存在: {video}")
        return video

    output_dir = ROOT / "output"
    candidates = sorted(output_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise DouyinPublishError("output/ 下没有 mp4")
    return candidates[0]


def main() -> int:
    load_env()

    venv_py = resolve_playwright_python(ROOT)
    if venv_py and Path(sys.executable).resolve() != venv_py.resolve() and not os.environ.get("AIVIDEO_PUBLISH_REEXEC"):
        os.environ["AIVIDEO_PUBLISH_REEXEC"] = "1"
        os.execv(str(venv_py), [str(venv_py), *sys.argv])

    parser = argparse.ArgumentParser(description="发布视频到抖音创作者平台")
    parser.add_argument("video", nargs="?", help="MP4 路径，默认 output 下最新文件")
    parser.add_argument("--script", default=str(ROOT / "logs" / "last_script.json"))
    parser.add_argument("--title", help="覆盖标题")
    parser.add_argument("--desc", help="覆盖简介")
    parser.add_argument("--tags", help="覆盖标签（逗号分隔）")
    parser.add_argument("--dry-run", action="store_true", help="只打印参数，不实际上传")
    parser.add_argument("--check", action="store_true", help="发布前先校验 cookie")
    parser.add_argument("--headed", action="store_true", help="有头 Chrome")
    parser.add_argument("--headless", action="store_true", help="无头模式")
    parser.add_argument(
        "--assist",
        action="store_true",
        help="半自动：脚本上传填表，你在浏览器里点「发布」",
    )
    args = parser.parse_args()

    try:
        video_path = resolve_video(args.video)
        script = load_script(Path(args.script))
        fields = build_sau_fields(script)

        title = args.title or fields["title"]
        desc = args.desc or fields["desc"]
        tags = args.tags if args.tags is not None else fields["tags"]

        print(f"账号: {douyin_account()}")
        print(f"视频: {video_path}")
        print(f"标题: {title}")
        print(f"简介: {desc}")
        if tags:
            print(f"标签: {tags}")

        if args.dry_run:
            return 0

        headed = None
        if args.headed:
            headed = True
        elif args.headless:
            headed = False

        if args.check:
            print("检查登录态…", flush=True)
            check_douyin_session(root=ROOT)
            print("登录态有效", flush=True)

        print("开始发布（约需 3–5 分钟）…", flush=True)
        asyncio.run(
            publish_video(
                video_path,
                title=title,
                desc=desc,
                tags=tags,
                root=ROOT,
                headed=headed,
                assist=args.assist,
            )
        )

        log_path = ROOT / "logs" / "last_douyin_publish.json"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            json.dumps(
                {
                    "method": "playwright",
                    "account": douyin_account(),
                    "video": str(video_path),
                    "title": title,
                    "desc": desc,
                    "tags": tags,
                    "assist": args.assist,
                    "published_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print("发布已提交（审核期间通常仅自己可见）")
        print(f"  记录: {log_path}")
        return 0
    except (DouyinPublishError, SauError) as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
