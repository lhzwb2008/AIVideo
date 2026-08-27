"""中英文流水线隔离：环境变量分层加载 + logs/output/archive 分目录。"""

from __future__ import annotations

import os
import re
from pathlib import Path

from paths import ROOT

LOCALES = ("zh", "en")


def normalize_locale(raw: str | None = None) -> str:
    v = (raw or os.environ.get("AIVIDEO_LOCALE", "zh")).strip().lower()
    if v in ("en", "english"):
        return "en"
    return "zh"


_SECTION_RE = re.compile(r"^#==\s*section:\s*(\w+)\s*==")

# 仅在某 locale 分块里定义；切换语言时需清掉，避免从另一套流水线残留
_LOCALE_SCOPED_KEYS = (
    "AIVIDEO_BRAND_NAME",
    "AIVIDEO_BRAND_TAGLINE",
    "AIVIDEO_OUTRO_HEADLINE",
    "AIVIDEO_OUTRO_SUBLINE",
    "AIVIDEO_OUTRO_NARRATION",
    "AIVIDEO_OUTRO_NARRATION_VARIANTS",
)


def _parse_env_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    key, _, val = line.partition("=")
    key = key.strip()
    val = val.strip().strip('"').strip("'")
    if not key:
        return None
    return key, val


def _apply_env_sections(path: Path, want_locale: str, *, force_overlay: bool) -> None:
    """按 .env 分块加载，行为与 scripts/load-dotenv.sh 一致。"""
    if not path.is_file():
        return
    section = "shared"
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = _SECTION_RE.match(raw.strip())
        if m:
            section = m.group(1).lower()
            continue
        parsed = _parse_env_line(raw)
        if not parsed:
            continue
        key, val = parsed
        if section == "shared":
            if key not in os.environ:
                os.environ[key] = val
        elif section == want_locale:
            if force_overlay or key not in os.environ:
                os.environ[key] = val


def load_locale_env(locale: str | None = None, *, force_overlay: bool = True) -> str:
    """加载 .env 分块：shared + 当前 locale（与 load-dotenv.sh 一致）。"""
    loc = normalize_locale(locale)
    os.environ["AIVIDEO_LOCALE"] = loc
    for key in _LOCALE_SCOPED_KEYS:
        os.environ.pop(key, None)
    _apply_env_sections(ROOT / ".env", loc, force_overlay=force_overlay)
    # 兼容旧版独立文件 .env.zh / .env.en
    _apply_env_sections(ROOT / f".env.{loc}", loc, force_overlay=True)
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


def host_intro_in_video() -> bool:
    """中文成片默认有吉祥物片头；英文没有。此时封面海报不进视频，也不该再去生图。"""
    if normalize_locale() == "en":
        return False
    raw = os.environ.get("AIVIDEO_HOST_INTRO", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


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
