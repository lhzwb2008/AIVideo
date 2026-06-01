"""发布文案终端展示（供手动复制到各平台）。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from douyin_caption import build_sau_fields
from paths import ROOT
from publish_resolve import load_script

UPLOAD_URLS: list[tuple[str, str]] = [
    ("抖音", "https://creator.douyin.com/creator-micro/content/upload"),
    ("小红书", "https://creator.xiaohongshu.com/publish/publish?from=homepage"),
    ("快手", "https://cp.kuaishou.com/article/publish/video"),
    ("视频号", "https://channels.weixin.qq.com/platform/post/create"),
    ("TikTok", "https://www.tiktok.com/upload"),
    ("YouTube", "https://studio.youtube.com/"),
]


def _load_script_dict(script_path: Path) -> dict | None:
    if not script_path.is_file():
        return None
    try:
        data = json.loads(script_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    script = data.get("script", data)
    return script if isinstance(script, dict) else None


def build_publish_fields(script: dict | None) -> dict[str, str]:
    return build_sau_fields(script)


def print_manual_publish_pack(
    script_path: Path,
    video_path: Path | None = None,
    *,
    youtube_url: str = "",
    tiktok_url: str = "",
    skip_auto_note: bool = False,
) -> None:
    script = _load_script_dict(script_path) or load_script(script_path)
    fields = build_publish_fields(script)
    tags = fields.get("tags") or ""
    hashtags = " ".join(f"#{t.strip()}" for t in tags.split(",") if t.strip())

    print("\n" + "═" * 58, flush=True)
    print("📋 发布文案（各平台通用，复制后按需微调）", flush=True)
    if video_path:
        try:
            rel = video_path.resolve().relative_to(ROOT.resolve())
            print(f"视频: {rel}", flush=True)
        except ValueError:
            print(f"视频: {video_path}", flush=True)
    if youtube_url:
        print(f"YouTube 已自动发布: {youtube_url}", flush=True)
    if tiktok_url:
        print(f"TikTok 已自动发布: {tiktok_url}", flush=True)
    if skip_auto_note and not youtube_url and not tiktok_url:
        print("（本次未自动发布 API 平台）", flush=True)
    print("═" * 58, flush=True)

    print(f"\n标题: {fields['title']}", flush=True)
    print(f"\n简介:\n{fields['desc']}", flush=True)
    if tags:
        print(f"\n标签: {tags}", flush=True)
    if hashtags:
        print(f"话题: {hashtags}", flush=True)

    print("\n创作者后台（收藏）:", flush=True)
    for name, url in UPLOAD_URLS:
        print(f"  {name}: {url}", flush=True)

    print("\n" + "─" * 58, flush=True)
    print("提示: 国内平台请真人手动上传与互动，勿用脚本模拟浏览器。", flush=True)
    print("─" * 58 + "\n", flush=True)


def youtube_enabled() -> bool:
    value = os.environ.get("AIVIDEO_PUBLISH_YOUTUBE")
    if value is None or value.strip() == "":
        return True
    return value.strip().lower() in ("1", "true", "yes", "on")


def tiktok_enabled() -> bool:
    value = os.environ.get("AIVIDEO_PUBLISH_TIKTOK")
    if value is None or value.strip() == "":
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")
