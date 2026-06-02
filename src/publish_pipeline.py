#!/usr/bin/env python3
"""制作发布共用流水线：脚本落地 → 生图 → 合成 → YouTube/TikTok API → 打印文案 → 归档。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from paths import ROOT
from publish_caption import (
    eastmoney_enabled,
    print_manual_publish_pack,
    tiktok_enabled,
    xueqiu_enabled,
    youtube_enabled,
)
from forum_auth import is_login_error
from research import run_article_research


def log(message: str) -> None:
    print(message, flush=True)


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve()))


def run(cmd: list[str], *, label: str) -> None:
    log(f"\n[{label}] {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=ROOT, env=os.environ.copy())
    if proc.returncode != 0:
        raise RuntimeError(f"{label} 失败，退出码 {proc.returncode}")


def read_script_title(script_path: Path) -> str:
    try:
        data = json.loads(script_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    script = data.get("script") or data
    return str(script.get("title") or "").strip()


def latest_video() -> Path:
    last_video = ROOT / "logs" / "last_video.txt"
    if not last_video.is_file():
        raise RuntimeError("未找到 logs/last_video.txt")
    raw = last_video.read_text(encoding="utf-8").strip()
    video = Path(raw)
    if not video.is_absolute():
        video = ROOT / video
    if not video.is_file():
        raise RuntimeError(f"视频文件不存在: {video}")
    return video


def _read_last_publish_url(log_name: str, *keys: str) -> str:
    log_path = ROOT / "logs" / log_name
    if not log_path.is_file():
        return ""
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    for key in keys:
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def publish_youtube_api(video: Path, script_path: Path, *, dry_run: bool) -> str:
    cmd = [
        str(ROOT / "scripts" / "publish-youtube.sh"),
        rel(video),
        "--script",
        rel(script_path),
    ]
    if dry_run:
        cmd.append("--dry-run")
    run(cmd, label="发布YouTube")
    if dry_run:
        return ""
    return _read_last_publish_url("last_youtube_publish.json", "shorts_url", "url")


def publish_tiktok_api(video: Path, script_path: Path, *, dry_run: bool) -> str:
    cmd = [
        str(ROOT / "scripts" / "publish-tiktok.sh"),
        rel(video),
        "--script",
        rel(script_path),
    ]
    if dry_run:
        cmd.append("--dry-run")
    run(cmd, label="发布TikTok")
    if dry_run:
        return ""
    return _read_last_publish_url("last_tiktok_publish.json", "url")


def publish_eastmoney_api(forum_dir: Path, *, dry_run: bool) -> str:
    cmd = [
        str(ROOT / "scripts" / "publish-eastmoney.sh"),
        rel(forum_dir),
    ]
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd.append("--publish")
    run(cmd, label="发布东方财富")
    return _read_last_publish_url("last_eastmoney_publish.json", "title")


def publish_xueqiu_api(forum_dir: Path, *, dry_run: bool) -> str:
    cmd = [
        str(ROOT / "scripts" / "publish-xueqiu.sh"),
        rel(forum_dir),
    ]
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd.append("--publish")
    run(cmd, label="发布雪球")
    return _read_last_publish_url("last_xueqiu_publish.json", "title")


def _retry_config() -> tuple[int, int]:
    """(最多尝试次数, 每次失败后等待秒数)。次数<=0 视为无限重试。"""
    try:
        max_attempts = int(os.environ.get("AIVIDEO_PUBLISH_MAX_RETRIES", "0"))
    except ValueError:
        max_attempts = 0
    try:
        sleep_s = int(os.environ.get("AIVIDEO_PUBLISH_RETRY_SLEEP", "20"))
    except ValueError:
        sleep_s = 20
    return max_attempts, max(1, sleep_s)


def _publish_with_retry(do_fn, *, label: str, dry_run: bool) -> str:
    """发布失败不退出：提示翻墙并一直重试，直到成功或达到上限。

    AIVIDEO_PUBLISH_MAX_RETRIES<=0（默认）= 无限重试；交互式终端可直接回车立即重试、
    输入 s 跳过本平台。
    """
    max_attempts, sleep_s = _retry_config()
    attempt = 0
    while True:
        attempt += 1
        try:
            return do_fn()
        except Exception as exc:  # noqa: BLE001
            log(f"  ⚠️ [{label}] 第 {attempt} 次发布失败：{exc}")
            if dry_run or (max_attempts > 0 and attempt >= max_attempts):
                log(f"  ↳ [{label}] 已达重试上限，跳过自动发布（不影响成片/手动发布）。")
                return ""
            remain = f"剩余 {max_attempts - attempt} 次" if max_attempts > 0 else "将持续重试"
            log(f"  🔌 [{label}] 请确认已【翻墙/开启代理】，{sleep_s}s 后自动重试…（{remain}）")
            if sys.stdin and sys.stdin.isatty():
                log(f"     （回车=立即重试；输入 s 回车=跳过 {label}）")
                if _wait_or_skip(sleep_s):
                    log(f"  ↳ [{label}] 已按要求跳过。")
                    return ""
            else:
                time.sleep(sleep_s)


def _wait_or_skip(sleep_s: int) -> bool:
    """交互式：最多等 sleep_s 秒。回车→立即重试(False)；输入 s→跳过(True)；超时→重试(False)。"""
    import select

    try:
        ready, _, _ = select.select([sys.stdin], [], [], sleep_s)
    except (OSError, ValueError):
        time.sleep(sleep_s)
        return False
    if not ready:
        return False
    line = sys.stdin.readline().strip().lower()
    return line == "s"


def publish_youtube(video: Path, script_path: Path, *, dry_run: bool) -> str:
    if not youtube_enabled():
        return ""

    def _do() -> str:
        url = publish_youtube_api(video, script_path, dry_run=dry_run)
        if url:
            log(f"  [YouTube] {url}")
        return url

    return _publish_with_retry(_do, label="YouTube", dry_run=dry_run)


def publish_tiktok(video: Path, script_path: Path, *, dry_run: bool) -> str:
    if not tiktok_enabled():
        return ""

    def _do() -> str:
        url = publish_tiktok_api(video, script_path, dry_run=dry_run)
        if url:
            log(f"  [TikTok] {url}")
        elif not dry_run:
            log("  [TikTok] 已上传到收件箱，请在 App 内粘贴终端文案后发布")
        return url

    return _publish_with_retry(_do, label="TikTok", dry_run=dry_run)


def _publish_forum_with_retry(do_fn, *, label: str, dry_run: bool) -> str:
    """论坛 Playwright 发布：cookie 失效已在内部等待扫码；其它错误可重试/跳过。"""
    max_attempts, sleep_s = _retry_config()
    attempt = 0
    while True:
        attempt += 1
        try:
            return do_fn()
        except Exception as exc:  # noqa: BLE001
            if is_login_error(exc):
                log(f"  🔐 [{label}] 登录问题：{exc}（应已弹窗等待扫码，正在重试…）")
                continue
            log(f"  ⚠️ [{label}] 第 {attempt} 次发布失败：{exc}")
            if dry_run or (max_attempts > 0 and attempt >= max_attempts):
                log(f"  ↳ [{label}] 已达重试上限，跳过自动发布（不影响成片/手动发布）。")
                return ""
            remain = f"剩余 {max_attempts - attempt} 次" if max_attempts > 0 else "将持续重试"
            log(f"  ↻ [{label}] {sleep_s}s 后自动重试…（{remain}）")
            if sys.stdin and sys.stdin.isatty():
                log(f"     （回车=立即重试；输入 s 回车=跳过 {label}）")
                if _wait_or_skip(sleep_s):
                    log(f"  ↳ [{label}] 已按要求跳过。")
                    return ""
            else:
                time.sleep(sleep_s)


def publish_xueqiu(forum_dir: str | Path, *, dry_run: bool) -> str:
    if not xueqiu_enabled():
        return ""
    path = Path(forum_dir)
    if not path.is_absolute():
        path = ROOT / path
    if not (path / "post.md").is_file():
        log(f"  ↳ [雪球] 跳过：无论坛包 {rel(path)}")
        return ""

    def _do() -> str:
        title = publish_xueqiu_api(path, dry_run=dry_run)
        if title:
            log(f"  [雪球] {title}")
        return title

    return _publish_forum_with_retry(_do, label="雪球", dry_run=dry_run)


def publish_eastmoney(forum_dir: str | Path, *, dry_run: bool) -> str:
    if not eastmoney_enabled():
        return ""
    path = Path(forum_dir)
    if not path.is_absolute():
        path = ROOT / path
    if not (path / "post.md").is_file():
        log(f"  ↳ [东方财富] 跳过：无论坛包 {rel(path)}")
        return ""

    def _do() -> str:
        title = publish_eastmoney_api(path, dry_run=dry_run)
        if title:
            log(f"  [东方财富] {title}")
        return title

    return _publish_forum_with_retry(_do, label="东方财富", dry_run=dry_run)


def archive_video(video: Path, *, date_tag: str) -> Path:
    """仅归档 mp4（兼容旧调用）；主流程请用 archive_publish_bundle。"""
    return archive_publish_bundle(video, date_tag=date_tag)["video"]


def archive_publish_bundle(video: Path, *, date_tag: str) -> dict[str, Path | None]:
    """归档 mp4 + 同名图文文件夹到 archive/published/YYYYMMDD/。"""
    from forum_manual_pack import forum_dir_for_video

    dest_dir = ROOT / "archive" / "published" / date_tag
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = video.stem

    video_target = dest_dir / video.name
    if video_target.exists():
        video_target = dest_dir / f"{stem}_{datetime.now().strftime('%H%M%S')}{video.suffix}"
    shutil.move(str(video), str(video_target))

    forum_target: Path | None = None
    forum_src = forum_dir_for_video(video)
    if forum_src.is_dir() and forum_src.resolve() != dest_dir.resolve():
        forum_target = dest_dir / stem
        if forum_target.exists():
            forum_target = dest_dir / f"{stem}_forum_{datetime.now().strftime('%H%M%S')}"
        shutil.move(str(forum_src), str(forum_target))

    return {"video": video_target, "forum": forum_target}


def generate_forum_pack(script_path: Path, video: Path) -> Path | None:
    if os.environ.get("AIVIDEO_FORUM_POST", "1").strip().lower() in ("0", "false", "no"):
        return None
    try:
        from forum_manual_pack import build_forum_pack, forum_dir_for_video

        forum_dir = forum_dir_for_video(video)
        build_forum_pack(script_path, video, forum_dir)
        log(f"论坛图文：{rel(forum_dir)}/（post.md + cover.jpg + cover_landscape.jpg + images/）")
        return forum_dir
    except Exception as exc:  # noqa: BLE001
        log(f"论坛图文生成跳过: {exc}")
        return None


def pipeline_after_script(
    script_path: Path,
    title: str,
    *,
    index: int,
    target: int,
    publish_check: bool,
    dry_run: bool,
    append_history_fn,
    skip_publish: bool = False,
) -> dict:
    del publish_check  # 保留参数兼容；国内平台改手动发布

    run([str(ROOT / "scripts" / "run-enrich-images.sh"), str(script_path)], label="生图")
    run([str(ROOT / "scripts" / "run-compose.sh"), str(script_path)], label="合成")
    video = latest_video()
    generate_forum_pack(script_path, video)

    if skip_publish:
        log(f"\n=== [{index}/{target}] 跳过自动发布（--no-publish）===")
        print_manual_publish_pack(script_path, video, skip_auto_note=True)
        return {"title": title, "video": rel(video), "script": rel(script_path), "published": False}

    youtube_url = ""
    tiktok_url = ""
    eastmoney_title = ""
    xueqiu_title = ""

    if dry_run:
        log(f"\n=== [{index}/{target}] 预演 API 发布 ===")
        youtube_url = publish_youtube(video, script_path, dry_run=True)
        tiktok_url = publish_tiktok(video, script_path, dry_run=True)
        forum_preview = video.parent / video.stem
        if forum_preview.is_dir() and (forum_preview / "post.md").is_file():
            eastmoney_title = publish_eastmoney(forum_preview, dry_run=True)
            xueqiu_title = publish_xueqiu(forum_preview, dry_run=True)
        print_manual_publish_pack(
            script_path,
            video,
            youtube_url=youtube_url,
            tiktok_url=tiktok_url,
            eastmoney_title=eastmoney_title,
            xueqiu_title=xueqiu_title,
            skip_auto_note=True,
        )
        return {
            "title": title,
            "video": rel(video),
            "script": rel(script_path),
            "published": False,
            "youtube_url": youtube_url,
            "tiktok_url": tiktok_url,
            "eastmoney_title": eastmoney_title,
            "xueqiu_title": xueqiu_title,
        }

    if youtube_enabled() or tiktok_enabled() or eastmoney_enabled() or xueqiu_enabled():
        log(f"\n=== [{index}/{target}] API 自动发布（YouTube / TikTok / 东方财富 / 雪球）===")
    youtube_url = publish_youtube(video, script_path, dry_run=False)
    tiktok_url = publish_tiktok(video, script_path, dry_run=False)

    append_history_fn(script_path)
    date_tag = datetime.now().strftime("%Y%m%d")
    archived = archive_publish_bundle(video, date_tag=date_tag)
    log(f"已归档：{rel(archived['video'])}")
    if archived.get("forum"):
        log(f"  论坛图文：{rel(archived['forum'])}/")
        log(f"  发布文案：{rel(archived['forum'])}/README.md")
        eastmoney_title = publish_eastmoney(archived["forum"], dry_run=False)
        xueqiu_title = publish_xueqiu(archived["forum"], dry_run=False)

    print_manual_publish_pack(
        script_path,
        archived["video"],
        youtube_url=youtube_url,
        tiktok_url=tiktok_url,
        eastmoney_title=eastmoney_title,
        xueqiu_title=xueqiu_title,
    )

    return {
        "title": title,
        "video": rel(archived["video"]),
        "forum": rel(archived["forum"]) if archived.get("forum") else "",
        "script": rel(script_path),
        "published": bool(youtube_url or tiktok_url or eastmoney_title or xueqiu_title),
        "youtube_url": youtube_url,
        "tiktok_url": tiktok_url,
        "eastmoney_title": eastmoney_title,
        "xueqiu_title": xueqiu_title,
    }


def process_topic(
    index: int,
    *,
    target: int,
    topic: dict,
    article: dict,
    details: dict,
    publish_check: bool,
    dry_run: bool,
    append_history_fn,
    skip_publish: bool = False,
) -> dict:
    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    script_path = logs_dir / f"last_script_{stamp}_topic{index:02d}.json"

    co = topic.get("cold_open") or ""
    log(f"\n=== [{index}/{target}] 话题：{topic.get('title_hint')} ===")
    if co:
        log(f"  冷开场: {co}")
    article = dict(article)
    article["_topic_plan"] = {
        "title_hint": topic.get("title_hint"),
        "cold_open": topic.get("cold_open"),
        "theme_cluster": topic.get("theme_cluster"),
        "angle": topic.get("angle"),
    }
    script, _ = run_article_research(
        output=script_path,
        auto_pick=True,
        source="exa",
        preselected_article=article,
        preselected_details=details,
        category=topic.get("category"),
    )
    title = str(script.get("title") or read_script_title(script_path) or "").strip()
    log(f"脚本标题：{title}")

    return pipeline_after_script(
        script_path,
        title,
        index=index,
        target=target,
        publish_check=publish_check,
        dry_run=dry_run,
        skip_publish=skip_publish,
        append_history_fn=append_history_fn,
    )
