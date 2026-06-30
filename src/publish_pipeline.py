#!/usr/bin/env python3
"""制作发布共用流水线：脚本落地 → 生图 → 合成 → 各平台 API → 打印文案 → 归档。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from locale_env import normalize_locale
from paths import ROOT
from publish_caption import (
    bilibili_enabled,
    douyin_enabled,
    eastmoney_enabled,
    facebook_enabled,
    instagram_enabled,
    linkedin_enabled,
    print_manual_publish_pack,
    shipinhao_enabled,
    tiktok_enabled,
    us_social_enabled,
    wechat_enabled,
    xhs_video_enabled,
    xueqiu_enabled,
    youtube_enabled,
    zhihu_enabled,
)
from publish_llm_browser import (
    llm_browser_default,
    publish_llm_browser,
    publish_llm_browser_forum,
)
from forum_auth import is_login_error
from invoke_script import script_argv
from research import run_article_research


def _locale_en() -> bool:
    return normalize_locale() == "en"


def _intl_video_publish_enabled() -> bool:
    """YouTube/TikTok/IG/FB/LinkedIn 仅英文 US 流水线使用。"""
    return _locale_en() and (
        youtube_enabled() or tiktok_enabled() or us_social_enabled()
    )


def log(message: str) -> None:
    print(message, flush=True)


# 本轮批量执行中，用户交互输入 s 跳过的发布渠道（同进程后续视频自动跳过）
_skipped_publish_labels: set[str] = set()


def reset_publish_skips() -> None:
    _skipped_publish_labels.clear()


def _mark_publish_skipped(label: str) -> None:
    _skipped_publish_labels.add(label)


def _publish_skipped(label: str) -> bool:
    return label in _skipped_publish_labels


def _auto_publish_platforms_label() -> str:
    names: list[str] = []
    if _locale_en():
        if youtube_enabled():
            names.append("YouTube")
        if tiktok_enabled():
            names.append("TikTok")
        if instagram_enabled():
            names.append("Instagram")
        if facebook_enabled():
            names.append("Facebook")
        if linkedin_enabled():
            names.append("LinkedIn")
    if bilibili_enabled():
        names.append("B站视频")
    if douyin_enabled() and llm_browser_default():
        names.append("抖音")
    if xhs_video_enabled() and llm_browser_default():
        names.append("小红书")
    if shipinhao_enabled():
        names.append("视频号")
    if eastmoney_enabled():
        names.append("东方财富")
    if xueqiu_enabled():
        names.append("雪球")
    if wechat_enabled():
        names.append("微信公众号")
    if zhihu_enabled():
        names.append("知乎专栏")
    return " / ".join(names) if names else ""


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.resolve()))


def run(cmd: list[str], *, label: str) -> None:
    log(f"\n[{label}] {' '.join(cmd)}")
    proc = subprocess.run(
        cmd, cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True
    )
    if proc.stdout:
        sys.stdout.write(proc.stdout)
        if not proc.stdout.endswith("\n"):
            sys.stdout.write("\n")
    if proc.stderr:
        sys.stderr.write(proc.stderr)
        if not proc.stderr.endswith("\n"):
            sys.stderr.write("\n")
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        msg = f"{label} 失败，退出码 {proc.returncode}"
        if detail:
            msg += f"\n{detail[-2000:]}"
        raise RuntimeError(msg)


def read_script_title(script_path: Path) -> str:
    try:
        data = json.loads(script_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    script = data.get("script") or data
    return str(script.get("title") or "").strip()


def latest_video() -> Path:
    from locale_env import locale_logs_dir

    last_video = locale_logs_dir() / "last_video.txt"
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
    from locale_env import locale_logs_dir

    log_path = locale_logs_dir() / log_name
    if not log_path.is_file():
        log_path = ROOT / "logs" / log_name
    if not log_path.is_file():
        return ""
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not keys:
        return ""
    if len(keys) >= 2 and isinstance(data.get(keys[0]), dict):
        cur: object = data
        for key in keys:
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(key)
        if cur is not None:
            return str(cur or "").strip()
    for key in keys:
        value = str(data.get(key) or "").strip()
        if value:
            return value
    return ""


def publish_youtube_api(video: Path, script_path: Path, *, dry_run: bool) -> str:
    cmd = script_argv(
        "publish-youtube",
        rel(video),
        "--script",
        rel(script_path),
    )
    if dry_run:
        cmd.append("--dry-run")
    run(cmd, label="发布YouTube")
    if dry_run:
        return ""
    return _read_last_publish_url("last_youtube_publish.json", "shorts_url", "url")


def _bilibili_skip_video() -> bool:
    raw = os.environ.get("BILIBILI_SKIP_VIDEO", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def publish_bilibili_api(
    video: Path,
    script_path: Path,
    *,
    dry_run: bool,
    skip_video: bool = False,
) -> str:
    if skip_video or _bilibili_skip_video():
        cmd = script_argv(
            "publish-bilibili",
            rel(video),
            "--script",
            rel(script_path),
            "--skip-video",
        )
        if dry_run:
            cmd.append("--dry-run")
        run(cmd, label="发布B站")
        if dry_run:
            return ""
        return _read_last_publish_url("last_bilibili_publish.json", "title")

    if llm_browser_default():
        return publish_llm_browser(
            "bilibili", video, script_path, dry_run=dry_run
        )

    cmd = script_argv(
        "publish-bilibili",
        rel(video),
        "--script",
        rel(script_path),
    )
    if skip_video or _bilibili_skip_video():
        cmd.append("--skip-video")
    if dry_run:
        cmd.append("--dry-run")
    run(cmd, label="发布B站")
    if dry_run:
        return ""
    return _read_last_publish_url("last_bilibili_publish.json", "title")


def publish_shipinhao_api(video: Path, script_path: Path, *, dry_run: bool) -> str:
    # PC WeChat client UI automation (long-lived login; replaces the browser flow
    # that required a daily QR re-scan on channels.weixin.qq.com).
    cmd = script_argv(
        "publish-shipinhao-pcwechat",
        "--video",
        rel(video),
        "--script",
        rel(script_path),
    )
    if dry_run:
        cmd.append("--dry-run")
    run(cmd, label="发布视频号")
    if dry_run:
        return ""
    return _read_last_publish_url("last_tencent_publish.json", "title")


def publish_douyin_api(video: Path, script_path: Path, *, dry_run: bool) -> str:
    return publish_llm_browser("douyin", video, script_path, dry_run=dry_run)


def publish_xiaohongshu_api(video: Path, script_path: Path, *, dry_run: bool) -> str:
    return publish_llm_browser("xiaohongshu", video, script_path, dry_run=dry_run)


def publish_tiktok_api(video: Path, script_path: Path, *, dry_run: bool) -> str:
    cmd = script_argv(
        "publish-tiktok",
        rel(video),
        "--script",
        rel(script_path),
    )
    if dry_run:
        cmd.append("--dry-run")
    run(cmd, label="发布TikTok")
    if dry_run:
        return ""
    return _read_last_publish_url("last_tiktok_publish.json", "url")


def publish_eastmoney_api(forum_dir: Path, *, dry_run: bool) -> str:
    cmd = script_argv("publish-eastmoney", rel(forum_dir))
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd.append("--publish")
    run(cmd, label="发布东方财富")
    return _read_last_publish_url("last_eastmoney_publish.json", "title")


def publish_xueqiu_api(forum_dir: Path, *, dry_run: bool) -> str:
    cmd = script_argv("publish-xueqiu", rel(forum_dir))
    if dry_run:
        cmd.append("--dry-run")
    else:
        cmd.append("--publish")
    run(cmd, label="发布雪球")
    return _read_last_publish_url("last_xueqiu_publish.json", "title")


def _wechat_draft_only() -> bool:
    raw = os.environ.get("WECHAT_DRAFT_ONLY", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def publish_wechat_api(forum_dir: Path, *, dry_run: bool) -> str:
    cmd = script_argv("publish-wechat", rel(forum_dir))
    if dry_run:
        cmd.append("--dry-run")
    elif not _wechat_draft_only():
        cmd.append("--publish")
    run(cmd, label="发布微信公众号")
    return _read_last_publish_url("last_wechat_publish.json", "title")


def _retry_config(*, llm_browser: bool = False) -> tuple[int, int]:
    """(最多尝试次数, 每次失败后等待秒数)。次数<=0 视为无限重试。"""
    env_key = (
        "AIVIDEO_LLM_PUBLISH_MAX_RETRIES"
        if llm_browser
        else "AIVIDEO_PUBLISH_MAX_RETRIES"
    )
    default = "2" if llm_browser else "0"
    try:
        max_attempts = int(os.environ.get(env_key, default))
    except ValueError:
        max_attempts = 2 if llm_browser else 0
    try:
        sleep_s = int(os.environ.get("AIVIDEO_PUBLISH_RETRY_SLEEP", "20"))
    except ValueError:
        sleep_s = 20
    if llm_browser:
        try:
            sleep_s = max(sleep_s, int(os.environ.get("LLM_BROWSER_PROFILE_COOLDOWN", "30")))
        except ValueError:
            sleep_s = max(sleep_s, 30)
    return max_attempts, max(1, sleep_s)


def _publish_with_retry(
    do_fn, *, label: str, dry_run: bool, llm_browser: bool = False
) -> str:
    """发布失败不退出：提示检查网络并一直重试，直到成功或达到上限。

    AIVIDEO_PUBLISH_MAX_RETRIES<=0（默认）= 无限重试；LLM 浏览器默认最多 2 次。
    """
    if _publish_skipped(label):
        log(f"  ↳ [{label}] 本轮已跳过（前序视频已标记 s）。")
        return ""
    max_attempts, sleep_s = _retry_config(llm_browser=llm_browser)
    attempt = 0
    while True:
        attempt += 1
        try:
            return do_fn()
        except Exception as exc:  # noqa: BLE001
            if _is_publish_config_error(exc):
                log(f"  ↳ [{label}] 环境/配置错误（{exc}），跳过自动发布。")
                return ""
            log(f"  ⚠️ [{label}] 第 {attempt} 次发布失败：{exc}")
            if llm_browser and not dry_run:
                recovered = _try_recover_llm_publish(label)
                if recovered:
                    log(f"  ✓ [{label}] 延迟日志已确认成功，跳过重试: {recovered}")
                    return recovered
            if dry_run or (max_attempts > 0 and attempt >= max_attempts):
                log(f"  ↳ [{label}] 已达重试上限，跳过自动发布（不影响成片/手动发布）。")
                return ""
            remain = f"剩余 {max_attempts - attempt} 次" if max_attempts > 0 else "将持续重试"
            log(f"  🔌 [{label}] 请确认网络/VPN 正常，{sleep_s}s 后自动重试…（{remain}）")
            if sys.stdin and sys.stdin.isatty():
                log(f"     （回车=立即重试；输入 s 回车=跳过 {label}）")
                if _wait_or_skip(sleep_s):
                    _mark_publish_skipped(label)
                    log(f"  ↳ [{label}] 已按要求跳过（本轮后续视频同渠道也将跳过）。")
                    return ""
            else:
                time.sleep(sleep_s)


def _try_recover_llm_publish(label: str) -> str:
    """子进程已实际发布但日志落盘慢时，避免重复投稿。"""
    from publish_llm_browser import wait_for_llm_publish_ok
    from paths import ROOT

    platform_map = {
        "抖音": "douyin",
        "小红书": "xiaohongshu",
        "视频号": "shipinhao",
        "B站": "bilibili",
    }
    platform = platform_map.get(label)
    if not platform:
        return ""
    try:
        wait_s = int(os.environ.get("LLM_BROWSER_RECONCILE_WAIT", "25"))
    except ValueError:
        wait_s = 25
    import json

    for log_path in (ROOT / "logs" / "zh" / f"last_llm_{platform}_publish.json", ROOT / "logs" / f"last_llm_{platform}_publish.json"):
        if not log_path.is_file():
            continue
        try:
            data = json.loads(log_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        video_raw = str(data.get("video") or "").strip()
        if not video_raw:
            continue
        video = Path(video_raw)
        if not video.is_file():
            continue
        try:
            return wait_for_llm_publish_ok(platform, video, timeout_s=wait_s)
        except RuntimeError:
            continue
    return ""


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
        from tiktok_auth import tiktok_direct_post_ready

        ready, reason = tiktok_direct_post_ready()
        if not ready:
            log(f"  ↳ [TikTok] 跳过自动发布：{reason}")
            return ""
        if dry_run:
            log(f"  ↳ [TikTok] Direct Post 就绪（{reason}），dry-run 不上传")
            return ""
        url = publish_tiktok_api(video, script_path, dry_run=False)
        if url:
            log(f"  [TikTok] {url}")
        else:
            log(f"  [TikTok] 直发完成（{reason}）")
        return url

    return _publish_with_retry(_do, label="TikTok", dry_run=dry_run)


def _us_social_headless() -> bool:
    raw = os.environ.get("US_SOCIAL_HEADLESS", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def publish_us_social_platform(
    platform: str,
    video: Path,
    script_path: Path,
    *,
    dry_run: bool,
) -> str:
    from publish_caption import facebook_enabled, instagram_enabled, linkedin_enabled
    from us_social_publish import PLATFORM_LABEL, publish_us_social

    enabled = {
        "instagram": instagram_enabled,
        "facebook": facebook_enabled,
        "linkedin": linkedin_enabled,
    }
    if platform not in enabled or not enabled[platform]():
        return ""

    label = PLATFORM_LABEL[platform]

    def _do() -> str:
        result = publish_us_social(
            platform,
            video,
            script_path,
            dry_run=dry_run,
            headless=_us_social_headless(),
        )
        if result:
            log(f"  [{label}] 发布成功")
        return result

    return _publish_with_retry(_do, label=label, dry_run=dry_run)


def publish_instagram(video: Path, script_path: Path, *, dry_run: bool) -> str:
    return publish_us_social_platform("instagram", video, script_path, dry_run=dry_run)


def publish_facebook_reels(video: Path, script_path: Path, *, dry_run: bool) -> str:
    return publish_us_social_platform("facebook", video, script_path, dry_run=dry_run)


def publish_linkedin(video: Path, script_path: Path, *, dry_run: bool) -> str:
    return publish_us_social_platform("linkedin", video, script_path, dry_run=dry_run)


def publish_bilibili(
    video: Path,
    script_path: Path,
    *,
    dry_run: bool,
    skip_video: bool = False,
) -> str:
    if not bilibili_enabled():
        return ""

    def _do() -> str:
        title = publish_bilibili_api(
            video,
            script_path,
            dry_run=dry_run,
            skip_video=skip_video,
        )
        if title:
            if _bilibili_skip_video() or skip_video:
                log(f"  [B站] 跳过视频上传: {title}")
            else:
                log(f"  [B站] 视频已提交: {title}")
        return title

    return _publish_forum_with_retry(
        _do, label="B站", dry_run=dry_run, non_retryable=_bilibili_non_retryable, llm_browser=True
    )


def publish_shipinhao(video: Path, script_path: Path, *, dry_run: bool) -> str:
    if not shipinhao_enabled():
        return ""

    def _do() -> str:
        title = publish_shipinhao_api(video, script_path, dry_run=dry_run)
        if title:
            log(f"  [视频号] 已提交: {title}")
        return title

    # PC WeChat UI automation (not the LLM browser flow): no browser profile cooldown
    # or browser-success reconciliation needed.
    return _publish_with_retry(_do, label="视频号", dry_run=dry_run, llm_browser=False)


def publish_douyin(video: Path, script_path: Path, *, dry_run: bool) -> str:
    if not douyin_enabled() or not llm_browser_default():
        return ""

    def _do() -> str:
        title = publish_douyin_api(video, script_path, dry_run=dry_run)
        if title:
            log(f"  [抖音] 已提交: {title}")
        return title

    return _publish_with_retry(_do, label="抖音", dry_run=dry_run, llm_browser=True)


def publish_xiaohongshu(video: Path, script_path: Path, *, dry_run: bool) -> str:
    if not xhs_video_enabled() or not llm_browser_default():
        return ""

    def _do() -> str:
        title = publish_xiaohongshu_api(video, script_path, dry_run=dry_run)
        if title:
            log(f"  [小红书] 已提交: {title}")
        return title

    return _publish_with_retry(_do, label="小红书", dry_run=dry_run, llm_browser=True)


def _bilibili_non_retryable(exc: BaseException) -> bool:
    from sau_client import bilibili_video_upload_skippable, is_sau_config_error

    msg = str(exc)
    return bilibili_video_upload_skippable(msg) or is_sau_config_error(msg)


def _is_publish_config_error(exc: BaseException) -> bool:
    from sau_client import is_sau_config_error

    return is_sau_config_error(str(exc))


def _publish_forum_with_retry(
    do_fn,
    *,
    label: str,
    dry_run: bool,
    non_retryable=None,
    llm_browser: bool = False,
) -> str:
    """论坛 Playwright 发布：cookie 失效已在内部等待扫码；其它错误可重试/跳过。"""
    if _publish_skipped(label):
        log(f"  ↳ [{label}] 本轮已跳过（前序视频已标记 s）。")
        return ""
    max_attempts, sleep_s = _retry_config(llm_browser=llm_browser)
    attempt = 0
    while True:
        attempt += 1
        try:
            return do_fn()
        except Exception as exc:  # noqa: BLE001
            if is_login_error(exc):
                log(f"  🔐 [{label}] 登录问题：{exc}（应已弹窗等待扫码，正在重试…）")
                continue
            if _is_publish_config_error(exc):
                log(f"  ↳ [{label}] 环境/配置错误（{exc}），跳过自动发布。")
                return ""
            if non_retryable and non_retryable(exc):
                log(f"  ↳ [{label}] 不可重试（{exc}），跳过本平台自动发布。")
                return ""
            log(f"  ⚠️ [{label}] 第 {attempt} 次发布失败：{exc}")
            if dry_run or (max_attempts > 0 and attempt >= max_attempts):
                log(f"  ↳ [{label}] 已达重试上限，跳过自动发布（不影响成片/手动发布）。")
                return ""
            remain = f"剩余 {max_attempts - attempt} 次" if max_attempts > 0 else "将持续重试"
            log(f"  ↻ [{label}] {sleep_s}s 后自动重试…（{remain}）")
            if sys.stdin and sys.stdin.isatty():
                log(f"     （回车=立即重试；输入 s 回车=跳过 {label}）")
                if _wait_or_skip(sleep_s):
                    _mark_publish_skipped(label)
                    log(f"  ↳ [{label}] 已按要求跳过（本轮后续视频同渠道也将跳过）。")
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


def publish_zhihu(forum_dir: str | Path, *, dry_run: bool) -> str:
    if not zhihu_enabled():
        return ""
    path = Path(forum_dir)
    if not path.is_absolute():
        path = ROOT / path
    if not (path / "post.md").is_file():
        log(f"  ↳ [知乎专栏] 跳过：无论坛包 {rel(path)}")
        return ""

    def _do() -> str:
        if llm_browser_default():
            title = publish_llm_browser_forum("zhihu", path, dry_run=dry_run)
        else:
            from publish_zhihu import publish_forum_dir

            title = publish_forum_dir(path, dry_run=dry_run)
        if title and not dry_run:
            log_path = ROOT / "logs" / "last_zhihu_publish.json"
            published = False
            url = ""
            if log_path.is_file():
                try:
                    payload = json.loads(log_path.read_text(encoding="utf-8"))
                    logged_pack = str(
                        payload.get("pack_dir") or payload.get("forum_dir") or ""
                    ).strip()
                    if logged_pack:
                        try:
                            if Path(logged_pack).resolve() != path.resolve():
                                logged_pack = ""
                        except OSError:
                            logged_pack = ""
                    if not logged_pack or Path(logged_pack).resolve() == path.resolve():
                        published = bool(payload.get("published"))
                        url = str(payload.get("url") or "").strip()
                except (OSError, json.JSONDecodeError):
                    pass
            if published:
                suffix = f" — {url}" if url else ""
                log(f"  [知乎专栏] 已发布: {title}{suffix}")
            else:
                log(f"  [知乎专栏] 草稿: {title}")
        return title

    return _publish_forum_with_retry(_do, label="知乎专栏", dry_run=dry_run)


def publish_wechat(forum_dir: str | Path, *, dry_run: bool) -> str:
    if not wechat_enabled():
        return ""
    path = Path(forum_dir)
    if not path.is_absolute():
        path = ROOT / path
    if not (path / "post.md").is_file():
        log(f"  ↳ [微信公众号] 跳过：无论坛包 {rel(path)}")
        return ""

    def _do() -> str:
        title = publish_wechat_api(path, dry_run=dry_run)
        if title:
            log_path = ROOT / "logs" / "last_wechat_publish.json"
            published = False
            note = ""
            if log_path.is_file():
                try:
                    payload = json.loads(log_path.read_text(encoding="utf-8"))
                    published = bool(payload.get("published"))
                    note = str(payload.get("publish_note") or "")
                except (OSError, json.JSONDecodeError):
                    pass
            label = "已发表" if published else "草稿"
            log(f"  [微信公众号] {label}: {title}")
            if note and not published:
                log(f"  ↳ {note}")
        return title

    return _publish_forum_with_retry(_do, label="微信公众号", dry_run=dry_run)


def archive_video(video: Path, *, date_tag: str) -> Path:
    """仅归档 mp4（兼容旧调用）；主流程请用 archive_publish_bundle。"""
    return archive_publish_bundle(video, date_tag=date_tag)["video"]


def archive_publish_bundle(video: Path, *, date_tag: str) -> dict[str, Path | None]:
    """归档 mp4 + 同名图文文件夹到 archive/published/YYYYMMDD/zh|en/。"""
    from forum_manual_pack import forum_dir_for_video
    from locale_env import archive_published_dir

    dest_dir = archive_published_dir(date_tag)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = video.stem

    video_target = dest_dir / video.name
    if video.resolve() == video_target.resolve():
        pass
    elif video_target.exists():
        video_target = dest_dir / f"{stem}_{datetime.now().strftime('%H%M%S')}{video.suffix}"
        shutil.move(str(video), str(video_target))
    else:
        shutil.move(str(video), str(video_target))
    caption_sidecar = video.with_suffix(".tiktok_caption.txt")
    if caption_sidecar.is_file():
        caption_target = video_target.with_suffix(".tiktok_caption.txt")
        try:
            shutil.move(str(caption_sidecar), str(caption_target))
        except OSError:
            pass

    forum_target: Path | None = None
    forum_src = forum_dir_for_video(video)
    if forum_src.is_dir():
        forum_target = dest_dir / stem
        if forum_src.resolve() == forum_target.resolve():
            pass
        elif forum_target.exists():
            forum_target = dest_dir / f"{stem}_forum_{datetime.now().strftime('%H%M%S')}"
            shutil.move(str(forum_src), str(forum_target))
        else:
            shutil.move(str(forum_src), str(forum_target))

    return {"video": video_target, "forum": forum_target}


def recover_missing_forum_packs(made: list[dict]) -> None:
    """批次结束后补救：有视频归档目录但缺 post.md 的，再尝试生成论坛图文。"""
    if not made:
        return
    fixed = 0
    for item in made:
        video_rel = str(item.get("video") or "").strip()
        script_rel = str(item.get("script") or "").strip()
        if not video_rel or not script_rel:
            continue
        video = (ROOT / video_rel).resolve()
        script_path = (ROOT / script_rel).resolve()
        if not video.is_file() or not script_path.is_file():
            continue
        forum_dir = video.parent / video.stem
        if (forum_dir / "post.md").is_file():
            continue
        title = item.get("title") or video.stem
        log(f"\n[补救] 论坛图文缺失 post.md，重试：{title}")
        if generate_forum_pack(script_path, video):
            fixed += 1
    if fixed:
        log(f"[补救] 已补全 {fixed} 个论坛图文包")


def generate_forum_pack(script_path: Path, video: Path) -> Path | None:
    if os.environ.get("AIVIDEO_FORUM_POST", "1").strip().lower() in ("0", "false", "no"):
        return None
    from forum_manual_pack import build_forum_pack, forum_dir_for_video

    forum_dir = forum_dir_for_video(video)
    rounds = max(1, int(os.environ.get("AIVIDEO_FORUM_PACK_ROUNDS", "3")))
    pause = max(1.0, float(os.environ.get("AIVIDEO_FORUM_PACK_RETRY_PAUSE", "8")))
    last_exc: Exception | None = None
    for rnd in range(rounds):
        allow_fallback = rnd == rounds - 1
        try:
            build_forum_pack(
                script_path,
                video,
                forum_dir,
                allow_fallback=allow_fallback,
            )
            post_md = forum_dir / "post.md"
            if not post_md.is_file():
                raise RuntimeError(f"论坛图文未生成 post.md: {forum_dir}")
            log(f"论坛图文：{rel(forum_dir)}/（post.md + cover.jpg + cover_landscape.jpg + images/）")
            return forum_dir
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if rnd + 1 < rounds:
                log(
                    f"论坛图文生成失败（{rnd + 1}/{rounds}），{pause:.0f}s 后重试：{exc}"
                )
                time.sleep(pause)
    log(f"论坛图文生成失败（已重试 {rounds} 次）: {last_exc}")
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
    skip_compose: bool = False,
    video: Path | None = None,
) -> dict:
    del publish_check  # 保留参数兼容；国内平台改手动发布
    if index == 1:
        reset_publish_skips()

    if skip_compose:
        from paths import resolve_video_for_publish

        if not video:
            raise RuntimeError("skip_compose 需要指定 video")
        video = resolve_video_for_publish(video)
        log(f"\n=== [{index}/{target}] 跳过生图/合成，使用已有视频 ===")
        log(f"  视频: {rel(video)}")
    else:
        run(script_argv("run-enrich-images", str(script_path)), label="生图")
        run(script_argv("run-compose", str(script_path)), label="合成")
        video = latest_video()

    forum_dir = video.parent / video.stem
    if (forum_dir / "post.md").is_file():
        log(f"论坛图文已存在：{rel(forum_dir)}/")
    else:
        generate_forum_pack(script_path, video)

    if skip_publish:
        log(f"\n=== [{index}/{target}] 跳过自动发布（--no-publish）===")
        print_manual_publish_pack(script_path, video, skip_auto_note=True)
        return {"title": title, "video": rel(video), "script": rel(script_path), "published": False}

    youtube_url = ""
    tiktok_url = ""
    instagram_ok = ""
    facebook_ok = ""
    linkedin_ok = ""
    bilibili_title = ""
    eastmoney_title = ""
    xueqiu_title = ""
    wechat_title = ""
    zhihu_title = ""
    shipinhao_title = ""
    douyin_title = ""
    xiaohongshu_title = ""

    if dry_run:
        log(f"\n=== [{index}/{target}] 预演 API 发布 ===")
        if _locale_en():
            youtube_url = publish_youtube(video, script_path, dry_run=True)
            tiktok_url = publish_tiktok(video, script_path, dry_run=True)
            instagram_ok = publish_instagram(video, script_path, dry_run=True)
            facebook_ok = publish_facebook_reels(video, script_path, dry_run=True)
            linkedin_ok = publish_linkedin(video, script_path, dry_run=True)
        bilibili_title = publish_bilibili(video, script_path, dry_run=True)
        douyin_title = publish_douyin(video, script_path, dry_run=True)
        xiaohongshu_title = publish_xiaohongshu(video, script_path, dry_run=True)
        shipinhao_title = publish_shipinhao(video, script_path, dry_run=True)
        forum_for_bili = video.parent / video.stem
        if (forum_for_bili / "post.md").is_file():
            wechat_title = publish_wechat(forum_for_bili, dry_run=True)
        forum_preview = video.parent / video.stem
        if forum_preview.is_dir() and (forum_preview / "post.md").is_file():
            eastmoney_title = publish_eastmoney(forum_preview, dry_run=True)
            xueqiu_title = publish_xueqiu(forum_preview, dry_run=True)
            zhihu_title = publish_zhihu(forum_preview, dry_run=True)
        print_manual_publish_pack(
            script_path,
            video,
            youtube_url=youtube_url,
            tiktok_url=tiktok_url,
            bilibili_title=bilibili_title,
            eastmoney_title=eastmoney_title,
            xueqiu_title=xueqiu_title,
            wechat_title=wechat_title,
            zhihu_title=zhihu_title,
            shipinhao_title=shipinhao_title,
            douyin_title=douyin_title,
            xiaohongshu_title=xiaohongshu_title,
            skip_auto_note=True,
        )
        return {
            "title": title,
            "video": rel(video),
            "script": rel(script_path),
            "published": False,
            "youtube_url": youtube_url,
            "tiktok_url": tiktok_url,
            "instagram": instagram_ok,
            "facebook": facebook_ok,
            "linkedin": linkedin_ok,
            "bilibili_title": bilibili_title,
            "douyin_title": douyin_title,
            "xiaohongshu_title": xiaohongshu_title,
            "shipinhao_title": shipinhao_title,
            "eastmoney_title": eastmoney_title,
            "xueqiu_title": xueqiu_title,
            "wechat_title": wechat_title,
            "zhihu_title": zhihu_title,
        }

    if (
        _intl_video_publish_enabled()
        or bilibili_enabled()
        or (douyin_enabled() and llm_browser_default())
        or (xhs_video_enabled() and llm_browser_default())
        or shipinhao_enabled()
        or wechat_enabled()
        or eastmoney_enabled()
        or xueqiu_enabled()
        or zhihu_enabled()
    ):
        label = _auto_publish_platforms_label()
        log(f"\n=== [{index}/{target}] API 自动发布（{label}）===")
    if _locale_en():
        youtube_url = publish_youtube(video, script_path, dry_run=False)
        tiktok_url = publish_tiktok(video, script_path, dry_run=False)
        instagram_ok = publish_instagram(video, script_path, dry_run=False)
        facebook_ok = publish_facebook_reels(video, script_path, dry_run=False)
        linkedin_ok = publish_linkedin(video, script_path, dry_run=False)
    bilibili_title = publish_bilibili(video, script_path, dry_run=False)
    douyin_title = publish_douyin(video, script_path, dry_run=False)
    xiaohongshu_title = publish_xiaohongshu(video, script_path, dry_run=False)
    shipinhao_title = publish_shipinhao(video, script_path, dry_run=False)

    forum_dir = video.parent / video.stem
    if forum_dir.is_dir() and (forum_dir / "post.md").is_file():
        wechat_title = publish_wechat(forum_dir, dry_run=False)
        eastmoney_title = publish_eastmoney(forum_dir, dry_run=False)
        xueqiu_title = publish_xueqiu(forum_dir, dry_run=False)
        zhihu_title = publish_zhihu(forum_dir, dry_run=False)

    from publish_llm_browser import reconcile_llm_publish_titles

    reconcile_wait = int(os.environ.get("LLM_BROWSER_RECONCILE_WAIT", "20"))
    if reconcile_wait > 0 and not (
        douyin_title and xiaohongshu_title and shipinhao_title
    ):
        log(f"  等待 {reconcile_wait}s，核对 LLM 发布日志（防 Chrome 延迟落盘）…")
        time.sleep(reconcile_wait)
    reconciled = reconcile_llm_publish_titles(
        video,
        douyin_title=douyin_title,
        xiaohongshu_title=xiaohongshu_title,
        shipinhao_title=shipinhao_title,
    )
    douyin_title = reconciled["douyin"]
    xiaohongshu_title = reconciled["xiaohongshu"]
    shipinhao_title = reconciled["shipinhao"]
    for plat, title in (
        ("抖音", douyin_title),
        ("小红书", xiaohongshu_title),
        ("视频号", shipinhao_title),
    ):
        if title:
            log(f"  [{plat}] 发布成功（日志确认）: {title}")

    append_history_fn(script_path)
    date_tag = datetime.now().strftime("%Y%m%d")
    archived = archive_publish_bundle(video, date_tag=date_tag)
    log(f"已归档：{rel(archived['video'])}")
    if archived.get("forum"):
        log(f"  论坛图文：{rel(archived['forum'])}/")
        log(f"  发布文案：{rel(archived['forum'])}/README.md")

    print_manual_publish_pack(
        script_path,
        archived["video"],
        youtube_url=youtube_url,
        tiktok_url=tiktok_url,
        bilibili_title=bilibili_title,
        eastmoney_title=eastmoney_title,
        xueqiu_title=xueqiu_title,
        wechat_title=wechat_title,
        zhihu_title=zhihu_title,
        shipinhao_title=shipinhao_title,
        douyin_title=douyin_title,
        xiaohongshu_title=xiaohongshu_title,
    )

    return {
        "title": title,
        "video": rel(archived["video"]),
        "forum": rel(archived["forum"]) if archived.get("forum") else "",
        "script": rel(script_path),
        "published": bool(
            (_locale_en() and (youtube_url or tiktok_url or instagram_ok or facebook_ok or linkedin_ok))
            or bilibili_title
            or douyin_title
            or xiaohongshu_title
            or shipinhao_title
            or wechat_title
            or eastmoney_title
            or xueqiu_title
            or zhihu_title
        ),
        "youtube_url": youtube_url,
        "tiktok_url": tiktok_url,
        "instagram": instagram_ok,
        "facebook": facebook_ok,
        "linkedin": linkedin_ok,
        "bilibili_title": bilibili_title,
        "douyin_title": douyin_title,
        "xiaohongshu_title": xiaohongshu_title,
        "shipinhao_title": shipinhao_title,
        "wechat_title": wechat_title,
        "eastmoney_title": eastmoney_title,
        "xueqiu_title": xueqiu_title,
        "zhihu_title": zhihu_title,
    }


def pipeline_publish_only(
    video: Path,
    *,
    script_path: Path | None = None,
    dry_run: bool = False,
    skip_publish: bool = False,
    append_history_fn=None,
) -> dict:
    """跳过选题/写稿/生图/合成，对已有 mp4 直接走发布（测试/补发）。"""
    from paths import resolve_video_for_publish
    from publish_resolve import load_script, resolve_script_for_video

    video = resolve_video_for_publish(video)
    script = script_path or resolve_script_for_video(video, None)
    if not script or not script.is_file():
        raise RuntimeError(
            f"找不到 {video.name} 的脚本 JSON，请加 --script logs\\last_script_....json"
        )
    data = load_script(script) or {}
    title = str(data.get("title") or read_script_title(script) or video.stem).strip()
    log(f"\n=== [publish-only] 直接发布（跳过生成）===")
    log(f"  视频: {rel(video)}")
    log(f"  脚本: {rel(script)}")
    log(f"  标题: {title}")

    from locale_env import locale_logs_dir

    loc = locale_logs_dir()
    loc.mkdir(parents=True, exist_ok=True)
    (loc / "last_video.txt").write_text(str(video.resolve()), encoding="utf-8")

    fn = append_history_fn or (lambda _: None)
    return pipeline_after_script(
        script,
        title,
        index=1,
        target=1,
        publish_check=False,
        dry_run=dry_run,
        skip_publish=skip_publish,
        append_history_fn=fn,
        skip_compose=True,
        video=video,
    )


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
    plan = {
        "title_hint": topic.get("title_hint"),
        "cold_open": topic.get("cold_open"),
        "theme_cluster": topic.get("theme_cluster"),
        "angle": topic.get("angle"),
        "direction": topic.get("direction"),
        "category": topic.get("category"),
        "slot": topic.get("slot") or topic.get("cursor_slot"),
        "script_mode": topic.get("script_mode"),
        "suggested_video_title": topic.get("suggested_video_title"),
        "fixed_video_title": topic.get("fixed_video_title"),
    }
    article["_topic_plan"] = {k: v for k, v in plan.items() if v}
    if topic.get("suggested_video_title"):
        article["_suggested_video_title"] = topic["suggested_video_title"]
    elif topic.get("fixed_video_title"):
        article["_fixed_video_title"] = topic["fixed_video_title"]
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
