"""US 流水线统一凭证目录（YouTube / TikTok / IG·FB·LinkedIn cookie）。

设置后（或默认 credentials/us/），各模块从该目录读写；本地保存后用
./scripts/pack-us-credentials.sh + scp 同步到服务器（勿提交 git，GitHub 会拦截 OAuth）。

目录结构::

    credentials/us/          # 默认 AIVIDEO_US_CREDENTIALS_DIR
      youtube/
        client_secret.json
        main_token.json
      tiktok/
        client.json
        main_token.json
      social/
        cookies/
          instagram_main.json
          facebook_main.json
          linkedin_main.json
          browser_profiles/
            instagram_main/
            ...
"""

from __future__ import annotations

import os
from pathlib import Path

from paths import ROOT

DEFAULT_REL = Path("credentials") / "us"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def us_credentials_root(*, create: bool = False) -> Path | None:
    raw = _env("AIVIDEO_US_CREDENTIALS_DIR")
    if not raw:
        return None
    root = Path(raw).expanduser()
    if not root.is_absolute():
        root = (ROOT / root).resolve()
    else:
        root = root.resolve()
    if create:
        ensure_layout(root)
    return root


def default_us_credentials_root() -> Path:
    """未显式配置时的推荐路径（credentials/us）。"""
    return (ROOT / DEFAULT_REL).resolve()


def resolved_us_credentials_root(*, create: bool = False) -> Path:
    return us_credentials_root(create=create) or default_us_credentials_root()


def ensure_layout(root: Path) -> None:
    for sub in (
        "youtube",
        "tiktok",
        "social/cookies/browser_profiles",
    ):
        (root / sub).mkdir(parents=True, exist_ok=True)


def apply_us_credentials_env(*, create: bool = False) -> Path:
    """统一使用 credentials/us（可被 git 跟踪，供服务器 pull）。"""
    root = us_credentials_root(create=create) or default_us_credentials_root()
    if create:
        ensure_layout(root)
    os.environ.setdefault("AIVIDEO_US_CREDENTIALS_DIR", str(root))
    os.environ.setdefault("YOUTUBE_CREDENTIALS_DIR", str(root / "youtube"))
    os.environ.setdefault("TIKTOK_CREDENTIALS_DIR", str(root / "tiktok"))
    os.environ.setdefault("AIVIDEO_US_SOCIAL_DIR", str(root / "social"))
    return root


def social_cookies_dir() -> Path:
    custom = _env("AIVIDEO_US_SOCIAL_DIR")
    if custom:
        return Path(custom).expanduser() / "cookies"
    sau = _env("SAU_HOME")
    if sau:
        return Path(sau).expanduser() / "cookies"
    return ROOT / "vendor" / "social-auto-upload" / "cookies"


def check_us_credentials(*, account: str = "main") -> dict[str, bool]:
    """检查各平台凭证文件是否存在。"""
    root = us_credentials_root() or default_us_credentials_root()
    apply_us_credentials_env()
    social = social_cookies_dir()
    return {
        "youtube_client": (root / "youtube" / "client_secret.json").is_file()
        or any((root / "youtube").glob("client_secret*.json")),
        "youtube_token": (root / "youtube" / f"{account}_token.json").is_file(),
        "tiktok_client": (root / "tiktok" / "client.json").is_file(),
        "tiktok_token": (root / "tiktok" / f"{account}_token.json").is_file(),
        "instagram": (social / f"instagram_{account}.json").is_file(),
        "facebook": (social / f"facebook_{account}.json").is_file(),
        "linkedin": (social / f"linkedin_{account}.json").is_file(),
    }
