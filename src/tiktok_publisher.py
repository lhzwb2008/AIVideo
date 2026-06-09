"""TikTok Content Posting API：Direct Post 上传 MP4。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from tiktok_auth import TikTokAuthError, _env, _http_session, _http_timeout, get_access_token


class TikTokPublishError(RuntimeError):
    pass


def _api_error(resp, body: dict, *, action: str) -> None:
    err = body.get("error") or {}
    code = err.get("code") or ""
    if resp.status_code >= 400 or (code and code != "ok"):
        msg = err.get("message") or resp.text[:500]
        raise TikTokPublishError(f"{action} 失败 ({code}): {msg}")


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=UTF-8",
    }


def query_creator_info(token: str | None = None) -> dict:
    token = token or get_access_token()
    session = _http_session()
    resp = session.post(
        "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
        headers=_headers(token),
        json={},
        timeout=_http_timeout(),
    )
    body = resp.json()
    _api_error(resp, body, action="creator_info/query")
    return body.get("data") or {}


def _resolve_privacy(creator_info: dict) -> str:
    preferred = (_env("TIKTOK_PRIVACY", "PUBLIC_TO_EVERYONE") or "PUBLIC_TO_EVERYONE").upper()
    options = [str(x).upper() for x in (creator_info.get("privacy_level_options") or [])]
    if preferred in options:
        return preferred
    if "SELF_ONLY" in options:
        return "SELF_ONLY"
    if options:
        return options[0]
    return "SELF_ONLY"


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if not value:
        return default
    return value.lower() in ("1", "true", "yes", "on")


def _chunk_plan(size: int) -> tuple[int, int]:
    """按 TikTok Media Transfer Guide 计算分片。"""
    max_single = 64 * 1024 * 1024
    if size <= max_single:
        return size, 1
    chunk_size = 10 * 1024 * 1024
    total = max(1, size // chunk_size)
    return chunk_size, total


def _upload_file(upload_url: str, video_path: Path, *, chunk_size: int, total_chunks: int) -> None:
    session = _http_session()
    data = video_path.read_bytes()
    size = len(data)
    for index in range(total_chunks):
        start = index * chunk_size
        end = min(size, start + chunk_size) - 1
        chunk = data[start : end + 1]
        headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(len(chunk)),
            "Content-Range": f"bytes {start}-{end}/{size}",
        }
        resp = session.put(upload_url, headers=headers, data=chunk, timeout=max(_http_timeout(), 600))
        if resp.status_code not in (200, 201, 204):
            raise TikTokPublishError(
                f"视频分片上传失败 chunk {index + 1}/{total_chunks}: "
                f"HTTP {resp.status_code} {resp.text[:300]}"
            )
        if total_chunks > 1:
            print(f"  上传分片 {index + 1}/{total_chunks}", flush=True)


def _poll_status(token: str, publish_id: str, *, mode: str = "direct") -> dict:
    session = _http_session()
    deadline = time.time() + max(120, int(_env("TIKTOK_PUBLISH_TIMEOUT", "900") or "900"))
    success_statuses = {"PUBLISH_COMPLETE"}
    if mode == "inbox":
        success_statuses.add("SEND_TO_USER_INBOX")
    last = {}
    while time.time() < deadline:
        resp = session.post(
            "https://open.tiktokapis.com/v2/post/publish/status/fetch/",
            headers=_headers(token),
            json={"publish_id": publish_id},
            timeout=_http_timeout(),
        )
        body = resp.json()
        _api_error(resp, body, action="status/fetch")
        last = body.get("data") or {}
        status = str(last.get("status") or "")
        print(f"  TikTok 状态: {status}", flush=True)
        if status in success_statuses:
            return last
        if status == "FAILED":
            reason = last.get("fail_reason") or "unknown"
            raise TikTokPublishError(f"TikTok 发布失败: {reason}")
        time.sleep(5)
    raise TikTokPublishError(f"TikTok 发布超时，最后状态: {last.get('status')}")


def _post_mode() -> str:
    mode = (_env("TIKTOK_POST_MODE", "direct") or "direct").strip().lower()
    if mode in ("inbox", "draft", "upload"):
        return "inbox"
    return "direct"


def _build_post_info(title: str, *, privacy_level: str = "") -> dict:
    """TikTok caption：title 字段支持正文 + #话题 + @mention（最多 2200 UTF-16）。"""
    post_info: dict[str, object] = {"title": title[:2200]}
    if privacy_level:
        post_info["privacy_level"] = privacy_level
    if _env_bool("TIKTOK_DISABLE_DUET", False):
        post_info["disable_duet"] = True
    if _env_bool("TIKTOK_DISABLE_STITCH", False):
        post_info["disable_stitch"] = True
    if _env_bool("TIKTOK_DISABLE_COMMENT", False):
        post_info["disable_comment"] = True
    if _env_bool("TIKTOK_BRAND_CONTENT", False):
        post_info["brand_content_toggle"] = True
    if _env_bool("TIKTOK_BRAND_ORGANIC", False):
        post_info["brand_organic_toggle"] = True
    if _env_bool("TIKTOK_DECLARE_AIGC", False):
        post_info["is_aigc"] = True
    return post_info


def upload_video(
    video_path: Path,
    *,
    title: str,
    privacy_level: str | None = None,
) -> dict:
    if not video_path.is_file():
        raise TikTokPublishError(f"视频不存在: {video_path}")

    token = get_access_token()
    mode = _post_mode()
    size = video_path.stat().st_size
    chunk_size, total_chunks = _chunk_plan(size)
    source_info = {
        "source": "FILE_UPLOAD",
        "video_size": size,
        "chunk_size": chunk_size,
        "total_chunk_count": total_chunks,
    }

    session = _http_session()
    if mode == "inbox":
        init_url = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
        # 收件箱模式也必须传 post_info.title，否则 App 里只剩应用名 #aivideo 占位
        payload = {
            "post_info": _build_post_info(title),
            "source_info": source_info,
            "post_mode": "MEDIA_UPLOAD",
        }
        privacy = ""
        username = ""
        print(f"  Caption 预填（收件箱）:\n{title[:500]}{'…' if len(title) > 500 else ''}", flush=True)
    else:
        creator = query_creator_info(token)
        privacy = privacy_level or _resolve_privacy(creator)
        username = str(creator.get("creator_username") or "").strip()
        init_url = "https://open.tiktokapis.com/v2/post/publish/video/init/"
        payload = {
            "post_info": _build_post_info(title, privacy_level=privacy),
            "source_info": source_info,
        }

    resp = session.post(
        init_url,
        headers=_headers(token),
        json=payload,
        timeout=_http_timeout(),
    )
    body = resp.json()
    err = body.get("error") or {}
    err_code = str(err.get("code") or "")
    if (
        mode == "inbox"
        and resp.status_code >= 400
        and "post_info" in payload
        and err_code in ("invalid_param", "param_error", "bad_request")
    ):
        # 旧版 API 可能不认 post_mode；降级重试仅 source_info
        print("  ⚠️  收件箱带 caption 初始化失败，降级为仅上传视频…", flush=True)
        resp = session.post(
            init_url,
            headers=_headers(token),
            json={"source_info": source_info},
            timeout=_http_timeout(),
        )
        body = resp.json()
    _api_error(resp, body, action="video/init")
    data = body.get("data") or {}
    publish_id = str(data.get("publish_id") or "")
    upload_url = str(data.get("upload_url") or "")
    if not publish_id or not upload_url:
        raise TikTokPublishError(f"init 未返回 publish_id/upload_url: {body}")

    label = "草稿到收件箱" if mode == "inbox" else "Direct Post"
    print(f"  正在上传视频到 TikTok（{label}）…", flush=True)
    _upload_file(upload_url, video_path, chunk_size=chunk_size, total_chunks=total_chunks)

    status = _poll_status(token, publish_id, mode=mode)
    post_ids = status.get("publicaly_available_post_id") or []
    post_id = str(post_ids[0]) if post_ids else ""
    url = f"https://www.tiktok.com/@{username}/video/{post_id}" if username and post_id else ""
    if mode == "inbox" and not url:
        url = "inbox:open TikTok app → Inbox to finish posting"
    return {
        "publish_id": publish_id,
        "post_id": post_id,
        "url": url,
        "privacy": privacy,
        "username": username,
        "mode": mode,
        "status": status,
    }
