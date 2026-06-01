"""YouTube Data API v3 上传视频（含 Shorts 元数据）。"""

from __future__ import annotations

import os
from pathlib import Path

from youtube_auth import YouTubeAuthError, build_youtube_service


class YouTubePublishError(RuntimeError):
    pass


def _set_thumbnail(video_id: str, thumbnail_path: Path, youtube) -> bool:
    """上传自定义封面；大文件走 requests + 代理更稳。"""
    suffix = thumbnail_path.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg"
    proxy = (
        _env("YOUTUBE_HTTP_PROXY")
        or _env("https_proxy")
        or _env("HTTPS_PROXY")
    )
    if proxy:
        return _set_thumbnail_requests(video_id, thumbnail_path, mime, proxy)
    from googleapiclient.http import MediaFileUpload

    thumb_media = MediaFileUpload(str(thumbnail_path), mimetype=mime)
    youtube.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()
    return True


def _set_thumbnail_requests(video_id: str, path: Path, mime: str, proxy: str) -> bool:
    import requests
    from google.auth.transport.requests import Request
    from youtube_auth import _load_credentials

    creds = _load_credentials()
    if not creds.valid:
        creds.refresh(Request())
    timeout = 300
    try:
        timeout = max(60, int(_env("YOUTUBE_HTTP_TIMEOUT", "300")))
    except ValueError:
        pass
    url = f"https://youtube.googleapis.com/upload/youtube/v3/thumbnails/set?videoId={video_id}"
    headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": mime}
    proxies = {"http": proxy, "https": proxy}
    data = path.read_bytes()
    resp = requests.post(url, headers=headers, data=data, proxies=proxies, timeout=timeout)
    if resp.status_code >= 400:
        raise YouTubePublishError(f"thumbnails.set HTTP {resp.status_code}: {resp.text[:500]}")
    return True


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _upload_video_requests(video_path: Path, body: dict, proxy: str) -> dict:
    """走 requests + 代理的 resumable 上传，绕开 httplib2 在代理下的
    RedirectMissingLocation 问题。返回 videos.insert 的 JSON 响应。"""
    import requests
    from google.auth.transport.requests import Request

    from youtube_auth import _load_credentials

    creds = _load_credentials()
    if not creds.valid:
        creds.refresh(Request())

    timeout = 600
    try:
        timeout = max(60, int(_env("YOUTUBE_HTTP_TIMEOUT", "600")))
    except ValueError:
        pass

    proxies = {"http": proxy, "https": proxy}
    file_size = video_path.stat().st_size

    init_url = (
        "https://youtube.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable&part=snippet,status"
    )
    init_headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "application/json; charset=UTF-8",
        "X-Upload-Content-Type": "video/mp4",
        "X-Upload-Content-Length": str(file_size),
    }
    init = requests.post(
        init_url, headers=init_headers, json=body, proxies=proxies, timeout=timeout
    )
    if init.status_code >= 400:
        raise YouTubePublishError(
            f"resumable init HTTP {init.status_code}: {init.text[:500]}"
        )
    session_url = init.headers.get("Location") or init.headers.get("location")
    if not session_url:
        raise YouTubePublishError("resumable init 未返回上传会话 URL（Location 头）")

    print(f"  上传中…（{file_size // 1024} KB，走代理 requests）", flush=True)
    put_headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-Type": "video/mp4",
        "Content-Length": str(file_size),
    }
    with video_path.open("rb") as fh:
        resp = requests.put(
            session_url,
            headers=put_headers,
            data=fh,
            proxies=proxies,
            timeout=timeout,
        )
    if resp.status_code not in (200, 201):
        raise YouTubePublishError(
            f"视频上传 HTTP {resp.status_code}: {resp.text[:500]}"
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise YouTubePublishError(f"上传成功但响应非 JSON: {resp.text[:300]}") from exc


def upload_video(
    video_path: Path,
    *,
    title: str,
    description: str,
    tags: list[str] | None = None,
    category_id: str | None = None,
    privacy_status: str | None = None,
    thumbnail_path: Path | None = None,
) -> dict:
    if not video_path.is_file():
        raise YouTubePublishError(f"视频不存在: {video_path}")

    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise YouTubePublishError(
            "缺少 google-api-python-client，请先运行: ./setup-youtube.sh"
        ) from exc

    privacy = (privacy_status or _env("YOUTUBE_PRIVACY", "public")).lower()
    if privacy not in ("public", "private", "unlisted"):
        raise YouTubePublishError(
            f"无效的 YOUTUBE_PRIVACY: {privacy}（可选 public/private/unlisted）"
        )

    category = category_id or _env("YOUTUBE_CATEGORY_ID", "25")
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "categoryId": str(category),
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    if tags:
        body["snippet"]["tags"] = [t[:30] for t in tags[:30] if t.strip()]

    # 代理下 httplib2 的 resumable 上传会因重定向缺 Location 头报错
    # （RedirectMissingLocation），改用 requests 直传更稳（与封面上传一致）。
    proxy = (
        _env("YOUTUBE_HTTP_PROXY")
        or _env("https_proxy")
        or _env("HTTPS_PROXY")
    )
    youtube = None
    if proxy:
        response = _upload_video_requests(video_path, body, proxy)
    else:
        youtube = build_youtube_service()
        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            resumable=True,
            chunksize=1024 * 1024 * 8,
        )
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                pct = int(status.progress() * 100)
                print(f"  上传进度: {pct}%", flush=True)

    video_id = response.get("id") or ""
    if not video_id:
        raise YouTubePublishError(f"上传完成但未返回 video id: {response}")

    if thumbnail_path and thumbnail_path.is_file():
        try:
            _set_thumbnail(video_id, thumbnail_path, youtube)
            print(f"  已设置封面: {thumbnail_path}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  ⚠️ 封面上传失败（视频已发布）: {exc}", flush=True)

    url = f"https://www.youtube.com/watch?v={video_id}"
    shorts_url = f"https://www.youtube.com/shorts/{video_id}"
    return {
        "video_id": video_id,
        "url": url,
        "shorts_url": shorts_url,
        "privacy": privacy,
        "response": response,
    }


def update_privacy(video_id: str, *, privacy_status: str = "public") -> dict:
    privacy = privacy_status.lower()
    if privacy not in ("public", "private", "unlisted"):
        raise YouTubePublishError(f"无效 privacy: {privacy_status}")

    youtube = build_youtube_service()
    body = {
        "id": video_id,
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    youtube.videos().update(part="status", body=body).execute()
    url = f"https://www.youtube.com/watch?v={video_id}"
    return {"video_id": video_id, "url": url, "privacy": privacy}


def update_video_metadata(
    video_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    thumbnail_path: Path | None = None,
) -> dict:
    youtube = build_youtube_service()
    if title or description or tags:
        resp = youtube.videos().list(part="snippet", id=video_id).execute()
        items = resp.get("items") or []
        if not items:
            raise YouTubePublishError(f"找不到视频: {video_id}")
        raw = items[0].get("snippet") or {}
        patch: dict = {}
        if title:
            patch["title"] = title[:100]
        if description:
            patch["description"] = description[:5000]
        if tags:
            patch["tags"] = [t[:30] for t in tags[:30] if t.strip()]
        # 保留原 categoryId（若有）；勿把 list 返回的只读字段整包提交
        cat = raw.get("categoryId")
        if cat:
            patch["categoryId"] = str(cat)
        youtube.videos().update(
            part="snippet",
            body={"id": video_id, "snippet": patch},
        ).execute()

    thumb_ok = False
    if thumbnail_path and thumbnail_path.is_file():
        try:
            thumb_ok = _set_thumbnail(video_id, thumbnail_path, youtube)
            print(f"  已设置封面: {thumbnail_path}", flush=True)
        except Exception as exc:  # noqa: BLE001
            err = str(exc)
            hint = (
                "频道需先满足 YouTube「验证手机号」等条件才能 API 设封面。"
                if "forbidden" in err.lower() or "permissions" in err.lower()
                else "上传超时或网络异常，请确认代理可用后重试。"
            )
            print(f"  ⚠️ 自定义封面未生效：{hint} ({exc})", flush=True)

    return {
        "video_id": video_id,
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "thumbnail_set": thumb_ok,
    }
