"""阿里云 OSS 上传 + 签名 URL，对齐 english-test/server/lib/ossUpload.mjs。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import mimetypes
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import formatdate
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def oss_configured() -> bool:
    return bool(_env("OSS_ACCESS_KEY_ID") and _env("OSS_ACCESS_KEY_SECRET") and _env("OSS_BUCKET"))


def _required(name: str) -> str:
    val = _env(name)
    if not val:
        raise RuntimeError(f"缺少环境变量 {name}")
    return val


def _bucket() -> str:
    return _required("OSS_BUCKET")


def _endpoint() -> str:
    return _env("OSS_ENDPOINT", "oss-cn-shanghai.aliyuncs.com").replace("https://", "").replace("http://", "").strip("/")


def _object_prefix() -> str:
    return _env("OSS_PREFIX", "wenbo").strip("/")


def _url_mode() -> str:
    return _env("OSS_URL_MODE", "signed").lower()


def _signed_seconds() -> int:
    raw = _env("OSS_SIGNED_URL_SECONDS") or _env("OSS_SIGNED_URL_SECONDS") or str(7 * 24 * 3600)
    try:
        n = int(raw)
    except ValueError:
        n = 7 * 24 * 3600
    return n if n > 60 else 7 * 24 * 3600


def _host() -> str:
    return f"{_bucket()}.{_endpoint()}"


def _sign(string_to_sign: str) -> str:
    secret = _required("OSS_ACCESS_KEY_SECRET").encode("utf-8")
    digest = hmac.new(secret, string_to_sign.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def _content_type(key: str) -> str:
    lower = key.lower()
    if lower.endswith(".mp3"):
        return "audio/mpeg"
    if lower.endswith(".wav"):
        return "audio/wav"
    if lower.endswith(".m4a"):
        return "audio/mp4"
    if lower.endswith(".mp4"):
        return "video/mp4"
    guessed, _ = mimetypes.guess_type(key)
    return guessed or "application/octet-stream"


@dataclass(frozen=True)
class OssObject:
    key: str
    url: str
    expires_at: str | None


def build_object_url(key: str) -> OssObject:
    key = key.lstrip("/")
    mode = _url_mode()
    if mode == "public":
        public_base = _env("OSS_PUBLIC_BASE_URL").rstrip("/")
        if public_base:
            url = f"{public_base}/{key}"
        else:
            ep = _env("OSS_PUBLIC_ENDPOINT", "oss-cn-shanghai.aliyuncs.com").replace("https://", "").replace("http://", "")
            url = f"https://{_bucket()}.{ep}/{key}"
        return OssObject(key=key, url=url, expires_at=None)

    seconds = _signed_seconds()
    expires = int(time.time()) + seconds
    resource = f"/{_bucket()}/{key}"
    string_to_sign = f"GET\n\n\n{expires}\n{resource}"
    signature = quote(_sign(string_to_sign), safe="")
    access_id = quote(_required("OSS_ACCESS_KEY_ID"), safe="")
    encoded_key = quote(key, safe="/")
    url = (
        f"https://{_host()}/{encoded_key}"
        f"?OSSAccessKeyId={access_id}&Expires={expires}&Signature={signature}"
    )
    expires_at = datetime.fromtimestamp(expires, tz=timezone.utc).isoformat()
    return OssObject(key=key, url=url, expires_at=expires_at)


def upload_file(local_path: Path, object_key: str) -> OssObject:
    path = Path(local_path)
    if not path.is_file():
        raise FileNotFoundError(f"本地文件不存在: {path}")
    key = object_key.lstrip("/")
    body = path.read_bytes()
    content_type = _content_type(key)
    date = formatdate(usegmt=True)
    resource = f"/{_bucket()}/{key}"
    string_to_sign = f"PUT\n\n{content_type}\n{date}\n{resource}"
    authorization = f"OSS {_required('OSS_ACCESS_KEY_ID')}:{_sign(string_to_sign)}"
    encoded_key = quote(key, safe="/")
    req = Request(
        f"https://{_host()}/{encoded_key}",
        data=body,
        method="PUT",
        headers={
            "Date": date,
            "Content-Type": content_type,
            "Content-Disposition": "inline",
            "Authorization": authorization,
        },
    )
    timeout = float(_env("OSS_TIMEOUT_MS", "120000")) / 1000.0
    with urlopen(req, timeout=timeout) as resp:
        if resp.status not in {200, 204}:
            raise RuntimeError(f"OSS 上传失败 status={resp.status}")
    return build_object_url(key)


def upload_host_intro_audio(local_path: Path) -> OssObject:
    path = Path(local_path)
    prefix = _object_prefix()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"{prefix}/aivideo/host-intro/{stamp}_{path.name}"
    return upload_file(path, key)
