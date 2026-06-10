"""TikTok Content Posting API OAuth2（Desktop Login Kit + PKCE）。"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from paths import ROOT

# Sandbox 默认只有 video.upload；Direct Post 需 App Review 通过后才有 video.publish
SCOPES = ["user.info.basic", "video.upload"]
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"


class TikTokAuthError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def account_name() -> str:
    return _env("TIKTOK_ACCOUNT", "main") or "main"


def credentials_dir() -> Path:
    custom = _env("TIKTOK_CREDENTIALS_DIR")
    if custom:
        return Path(custom).expanduser()
    return ROOT / "credentials" / "tiktok"


def client_config_path() -> Path:
    custom = _env("TIKTOK_CLIENT_CONFIG")
    if custom:
        return Path(custom).expanduser()
    return credentials_dir() / "client.json"


def token_path() -> Path:
    custom = _env("TIKTOK_TOKEN_PATH")
    if custom:
        return Path(custom).expanduser()
    return credentials_dir() / f"{account_name()}_token.json"


def redirect_uri() -> str:
    custom = _env("TIKTOK_REDIRECT_URI")
    if custom:
        return custom
    port = _oauth_port()
    return f"http://127.0.0.1:{port}/callback/"


def _oauth_port() -> int:
    raw = _env("TIKTOK_OAUTH_PORT", "8765")
    try:
        return max(1024, int(raw))
    except ValueError:
        return 8765


def _load_client_config() -> dict:
    path = client_config_path()
    if not path.is_file():
        raise TikTokAuthError(
            f"未找到 TikTok 应用配置: {path}\n"
            "请在 TikTok for Developers 创建应用，保存 client_key/client_secret 到:\n"
            "  credentials/tiktok/client.json\n"
            "并在 Login Kit 注册 redirect_uri（默认 http://127.0.0.1:8765/callback/）"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    key = str(data.get("client_key") or data.get("clientKey") or "").strip()
    secret = str(data.get("client_secret") or data.get("clientSecret") or "").strip()
    if not key or not secret:
        raise TikTokAuthError(f"{path} 需包含 client_key 与 client_secret")
    return {"client_key": key, "client_secret": secret}


def _http_session():
    import requests

    session = requests.Session()
    proxy = (
        _env("TIKTOK_HTTP_PROXY")
        or _env("https_proxy")
        or _env("HTTPS_PROXY")
        or _env("http_proxy")
        or _env("HTTP_PROXY")
    )
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def _http_timeout() -> int:
    try:
        return max(30, int(_env("TIKTOK_HTTP_TIMEOUT", "120")))
    except ValueError:
        return 120


def _generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    challenge = hashlib.sha256(verifier.encode("ascii")).hexdigest()
    return verifier, challenge


def _save_token(data: dict) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_token_file() -> dict | None:
    path = token_path()
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _token_request(payload: dict) -> dict:
    import requests

    session = _http_session()
    resp = session.post(
        TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=_http_timeout(),
    )
    data = resp.json()
    if resp.status_code >= 400 or data.get("error"):
        desc = data.get("error_description") or data.get("error") or resp.text[:300]
        raise TikTokAuthError(f"OAuth token 请求失败: {desc}")
    return data


def refresh_access_token(token_data: dict) -> dict:
    cfg = _load_client_config()
    refresh = str(token_data.get("refresh_token") or "").strip()
    if not refresh:
        raise TikTokAuthError("token 缺少 refresh_token，请重新授权: ./tiktok-login.sh --force")
    data = _token_request(
        {
            "client_key": cfg["client_key"],
            "client_secret": cfg["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": refresh,
        }
    )
    merged = {**token_data, **data}
    _save_token(merged)
    return merged


def get_access_token(*, force_refresh: bool = False) -> str:
    token_data = _load_token_file()
    if not token_data:
        raise TikTokAuthError(
            f"未找到 token: {token_path()}\n请先运行: ./tiktok-login.sh"
        )

    access = str(token_data.get("access_token") or "").strip()
    expires_in = int(token_data.get("expires_in") or 0)
    updated_at = float(token_data.get("_updated_at") or 0)
    import time

    stale = force_refresh or not access
    if not stale and expires_in > 0 and updated_at:
        stale = time.time() >= updated_at + max(60, expires_in - 120)

    if stale:
        token_data = refresh_access_token(token_data)
        access = str(token_data.get("access_token") or "").strip()

    if not access:
        raise TikTokAuthError("无法获取有效 access_token，请运行: ./tiktok-login.sh --force")
    return access


def run_login(*, force: bool = False) -> int:
    if force and token_path().is_file():
        token_path().unlink()

    cfg = _load_client_config()
    verifier, challenge = _generate_pkce()
    state = secrets.token_urlsafe(24)
    redirect = redirect_uri()
    scope = _env("TIKTOK_SCOPES", ",".join(SCOPES))

    params = {
        "client_key": cfg["client_key"],
        "scope": scope,
        "response_type": "code",
        "redirect_uri": redirect,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    auth_link = AUTH_URL + "?" + urllib.parse.urlencode(params)

    result: dict = {}
    error: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if not parsed.path.rstrip("/").endswith("/callback"):
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("state", [""])[0] != state:
                error.append("state 不匹配，可能存在 CSRF")
            elif qs.get("error"):
                error.append(qs.get("error_description", qs["error"])[0])
            elif qs.get("code"):
                result["code"] = qs["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            msg = "✅ TikTok 授权成功，可以关闭此页面。" if result.get("code") else "❌ 授权失败，请回到终端查看。"
            self.wfile.write(f"<html><body><h2>{msg}</h2></body></html>".encode())

        def log_message(self, *_args):  # noqa: D401
            return

    port = _oauth_port()
    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    print(f"正在打开浏览器授权 TikTok… redirect_uri={redirect}", flush=True)
    print(f"若浏览器未自动打开，请访问:\n{auth_link}\n", flush=True)
    webbrowser.open(auth_link)
    thread.join(timeout=300)
    server.server_close()

    if error:
        raise TikTokAuthError(error[0])
    code = result.get("code")
    if not code:
        raise TikTokAuthError("未收到授权 code（超时或 redirect_uri 与开发者后台不一致）")

    data = _token_request(
        {
            "client_key": cfg["client_key"],
            "client_secret": cfg["client_secret"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect,
            "code_verifier": verifier,
        }
    )
    import time

    data["_updated_at"] = time.time()
    _save_token(data)
    print(f"✅ TikTok 授权已保存: {token_path()}", flush=True)
    print(f"   scope: {data.get('scope', '')}", flush=True)
    return 0


_direct_post_ready_cache: tuple[bool, str] | None = None


def reset_tiktok_direct_post_cache() -> None:
    global _direct_post_ready_cache
    _direct_post_ready_cache = None


def tiktok_direct_post_ready(*, refresh: bool = False) -> tuple[bool, str]:
    """判断 TikTok Direct Post 自动直发是否就绪（非收件箱/草稿模式）。

    流水线在未就绪时会跳过 TikTok，避免把视频堆进 App 草稿箱。
    """
    global _direct_post_ready_cache
    if _direct_post_ready_cache is not None and not refresh:
        return _direct_post_ready_cache

    mode = (_env("TIKTOK_POST_MODE", "direct") or "direct").strip().lower()
    if mode in ("inbox", "draft", "upload"):
        result = (False, f"TIKTOK_POST_MODE={mode}（收件箱模式，非自动直发）")
        _direct_post_ready_cache = result
        return result

    token_data = _load_token_file()
    if not token_data:
        result = (False, "未授权 TikTok token（运行 ./tiktok-login.sh）")
        _direct_post_ready_cache = result
        return result

    scopes = str(token_data.get("scope") or "")
    if "video.publish" not in scopes:
        result = (
            False,
            f"token 缺少 video.publish scope（当前: {scopes or '无'}；"
            "过审后重新授权: TIKTOK_SCOPES=user.info.basic,video.upload,video.publish ./tiktok-login.sh --force）",
        )
        _direct_post_ready_cache = result
        return result

    try:
        token = get_access_token()
    except TikTokAuthError as exc:
        result = (False, str(exc))
        _direct_post_ready_cache = result
        return result

    session = _http_session()
    resp = session.post(
        "https://open.tiktokapis.com/v2/post/publish/creator_info/query/",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={},
        timeout=_http_timeout(),
    )
    body = resp.json()
    err = (body.get("error") or {}).get("code") or ""
    if err == "scope_not_authorized":
        result = (False, "video.publish scope 未获用户授权")
        _direct_post_ready_cache = result
        return result
    if resp.status_code >= 400 or (err and err != "ok"):
        result = (False, f"creator_info 查询失败: {body}")
        _direct_post_ready_cache = result
        return result

    info = body.get("data") or {}
    privacy_options = [str(x).upper() for x in (info.get("privacy_level_options") or [])]
    preferred = (_env("TIKTOK_PRIVACY", "PUBLIC_TO_EVERYONE") or "PUBLIC_TO_EVERYONE").upper()
    if preferred not in privacy_options:
        if privacy_options:
            result = (
                False,
                f"无法使用 TIKTOK_PRIVACY={preferred}（可用: {', '.join(privacy_options)}；"
                "应用未过审时通常仅 SELF_ONLY）",
            )
        else:
            result = (False, "creator_info 未返回 privacy_level_options")
        _direct_post_ready_cache = result
        return result

    username = str(info.get("creator_username") or "?").strip()
    result = (True, f"@{username} · privacy={preferred}")
    _direct_post_ready_cache = result
    return result


def run_check() -> int:
    token_data = _load_token_file()
    if not token_data:
        raise TikTokAuthError(f"未找到 token: {token_path()}\n请先运行: ./tiktok-login.sh")
    scopes = str(token_data.get("scope") or "")
    mode = (_env("TIKTOK_POST_MODE", "direct") or "direct").strip().lower()
    if mode in ("inbox", "draft", "upload"):
        if "video.upload" not in scopes:
            raise TikTokAuthError(f"token 缺少 video.upload scope: {scopes}")
        print(f"✅ TikTok token 有效 — scope: {scopes}（inbox 模式）", flush=True)
        print("   上传成功后请在 TikTok App → 收件箱/Inbox 完成发布。", flush=True)
        print("   ⚠️  自动流水线会跳过 TikTok（未开启 Direct Post）。", flush=True)
        return 0

    ready, detail = tiktok_direct_post_ready(refresh=True)
    if not ready:
        raise TikTokAuthError(f"TikTok Direct Post 未就绪: {detail}")
    print(f"✅ TikTok Direct Post 就绪 — {detail}", flush=True)
    print(f"   scope: {scopes}", flush=True)
    return 0
