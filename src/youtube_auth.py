"""YouTube Data API OAuth2：登录、刷新 token、构建 API 客户端。"""

from __future__ import annotations

import json
import os
from pathlib import Path

from paths import ROOT

# force-ssl：上传 + 改可见性/元数据（仅 youtube.upload 无法 videos.update）
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]


def _http_timeout() -> int:
    try:
        return max(30, int(_env("YOUTUBE_HTTP_TIMEOUT", "180")))
    except ValueError:
        return 180


def _wrap_request_with_timeout(request_fn, timeout: int | None = None):
    t = timeout or _http_timeout()

    def wrapped(method, url, **kwargs):
        kwargs.setdefault("timeout", t)
        return request_fn(method, url, **kwargs)

    return wrapped


class YouTubeAuthError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def account_name() -> str:
    return _env("YOUTUBE_ACCOUNT", "main") or "main"


def credentials_dir() -> Path:
    custom = _env("YOUTUBE_CREDENTIALS_DIR")
    if custom:
        return Path(custom).expanduser()
    return ROOT / "credentials" / "youtube"


def client_secrets_path() -> Path:
    custom = _env("YOUTUBE_CLIENT_SECRETS")
    if custom:
        return Path(custom).expanduser()
    default = credentials_dir() / "client_secret.json"
    if default.is_file():
        return default
    # Google 下载的文件名常带随机后缀
    candidates = sorted(credentials_dir().glob("client_secret*.json"))
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise YouTubeAuthError(
            f"credentials/youtube/ 下有多个 client_secret*.json，"
            f"请只保留一个或设置 YOUTUBE_CLIENT_SECRETS"
        )
    return default


def token_path() -> Path:
    custom = _env("YOUTUBE_TOKEN_PATH")
    if custom:
        return Path(custom).expanduser()
    return credentials_dir() / f"{account_name()}_token.json"


def _load_credentials():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise YouTubeAuthError(
            "缺少 Google API 依赖，请先运行: ./setup-youtube.sh"
        ) from exc

    path = token_path()
    creds = None
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        creds = Credentials.from_authorized_user_info(data, SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            import requests

            req = Request(session=requests.Session())
            req.session.request = _wrap_request_with_timeout(req.session.request)  # type: ignore[method-assign]
            creds.refresh(req)
        except OSError as exc:
            raise YouTubeAuthError(
                "刷新 token 失败，本机可能无法访问 Google API。"
                " 请开代理后重试，或重新运行 ./youtube-login.sh --force"
            ) from exc
        _save_credentials(creds)
        return creds

    secrets = client_secrets_path()
    if not secrets.is_file():
        raise YouTubeAuthError(
            f"未找到 OAuth 客户端文件: {secrets}\n"
            "请在 Google Cloud Console 创建「桌面应用」OAuth 凭据，"
            "下载 JSON 并保存为 credentials/youtube/client_secret.json\n"
            "或设置环境变量 YOUTUBE_CLIENT_SECRETS=路径"
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    # 走 requests 默认超时，避免国内网络卡住时无限等待
    try:
        import requests

        sess = requests.Session()
        sess.request = _wrap_request_with_timeout(sess.request)  # type: ignore[method-assign]
        flow.oauth2session.session = sess
    except Exception:
        pass
    port_raw = _env("YOUTUBE_OAUTH_PORT", "0")
    port = int(port_raw) if port_raw.isdigit() else 0
    print(
        "正在打开浏览器授权…（若浏览器已成功但此处超时，请开代理后重跑 ./youtube-login.sh --force）",
        flush=True,
    )
    creds = flow.run_local_server(port=port, prompt="consent", open_browser=True)
    _save_credentials(creds)
    return creds


def _save_credentials(creds) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json(), encoding="utf-8")


def run_login(*, force: bool = False) -> int:
    if force and token_path().is_file():
        token_path().unlink()
    _load_credentials()
    print(f"✅ YouTube 授权已保存: {token_path()}", flush=True)
    return 0


def run_check() -> int:
    path = token_path()
    if not path.is_file():
        raise YouTubeAuthError(
            f"未找到 token: {path}\n"
            "浏览器若已显示授权成功，但终端报错/超时，说明本机连不上 oauth2.googleapis.com。\n"
            "请开代理后重跑: ./youtube-login.sh --force"
        )
    creds = _load_credentials()
    if not creds.valid:
        raise YouTubeAuthError("token 无效或已过期，请运行: ./youtube-login.sh --force")
    # youtube.upload 不含 channels.list 权限；能刷新/加载即视为可上传
    print(f"✅ YouTube token 有效 — {path}", flush=True)
    return 0


def _build_http(creds):
    """googleapiclient 默认 httplib2 不读 http_proxy，需显式配置。"""
    import httplib2
    from google_auth_httplib2 import AuthorizedHttp

    timeout = _http_timeout()
    proxy_url = (
        _env("YOUTUBE_HTTP_PROXY")
        or _env("https_proxy")
        or _env("HTTPS_PROXY")
        or _env("http_proxy")
        or _env("HTTP_PROXY")
    )
    if proxy_url:
        from urllib.parse import urlparse

        parsed = urlparse(proxy_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (7897 if host == "127.0.0.1" else 8080)
        # 3 = HTTP proxy（不依赖 PySocks）
        proxy_info = httplib2.ProxyInfo(3, host, port)
        http = httplib2.Http(proxy_info=proxy_info, timeout=timeout)
    else:
        http = httplib2.Http(timeout=timeout)
    return AuthorizedHttp(creds, http=http)


def build_youtube_service():
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise YouTubeAuthError(
            "缺少 google-api-python-client，请先运行: ./setup-youtube.sh"
        ) from exc

    creds = _load_credentials()
    return build("youtube", "v3", http=_build_http(creds), cache_discovery=False)
