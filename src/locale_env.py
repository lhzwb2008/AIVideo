"""中英文流水线隔离：环境变量分层加载 + logs/output/archive 分目录。"""

from __future__ import annotations

import os
from pathlib import Path

from paths import ROOT

LOCALES = ("zh", "en")


def normalize_locale(raw: str | None = None) -> str:
    v = (raw or os.environ.get("AIVIDEO_LOCALE", "zh")).strip().lower()
    if v in ("en", "english"):
        return "en"
    return "zh"


def _apply_env_file(path: Path, *, force: bool) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if not key:
            continue
        if force or key not in os.environ:
            os.environ[key] = val


def load_locale_env(locale: str | None = None, *, force_overlay: bool = True) -> str:
    """加载 .env（共享密钥）+ .env.{zh|en}（语言专属配置，默认覆盖同名项）。"""
    loc = normalize_locale(locale)
    os.environ["AIVIDEO_LOCALE"] = loc
    _apply_env_file(ROOT / ".env", force=False)
    _apply_env_file(ROOT / f".env.{loc}", force=force_overlay)
    if loc == "zh":
        # 中文流水线走国内平台；YouTube/TikTok 仅 make-us-publish.sh（.env.en）
        os.environ["AIVIDEO_PUBLISH_YOUTUBE"] = "0"
        os.environ["AIVIDEO_PUBLISH_TIKTOK"] = "0"
    return loc


def locale_logs_dir(locale: str | None = None) -> Path:
    p = ROOT / "logs" / normalize_locale(locale)
    p.mkdir(parents=True, exist_ok=True)
    return p


def locale_output_dir(locale: str | None = None) -> Path:
    p = ROOT / "output" / normalize_locale(locale)
    p.mkdir(parents=True, exist_ok=True)
    return p


def archive_published_dir(date_tag: str, locale: str | None = None) -> Path:
    """archive/published/YYYYMMDD/zh|en/"""
    p = ROOT / "archive" / "published" / date_tag / normalize_locale(locale)
    p.mkdir(parents=True, exist_ok=True)
    return p


def latest_output_video(locale: str | None = None) -> Path | None:
    """当前 locale 的 output/{locale}/ 下最新 mp4；兼容旧版 output/ 根目录。"""
    loc_dir = locale_output_dir(locale)
    candidates = sorted(loc_dir.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    if candidates:
        return candidates[0]
    legacy = ROOT / "output"
    if legacy.is_dir():
        legacy_candidates = sorted(legacy.glob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
        if legacy_candidates:
            return legacy_candidates[0]
    return None


def iter_script_json_paths() -> list[Path]:
    """当前 locale 的脚本 + 兼容旧版 logs/ 根目录。"""
    loc_dir = locale_logs_dir()
    patterns = [
        loc_dir.glob("last_script_*.json"),
        loc_dir.glob("cursor_research_*.json"),
        (ROOT / "logs").glob("last_script_*.json"),
        (ROOT / "logs").glob("cursor_research_*.json"),
    ]
    seen: set[str] = set()
    out: list[Path] = []
    for gen in patterns:
        for p in sorted(gen, key=lambda x: x.stat().st_mtime, reverse=True):
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    return out
