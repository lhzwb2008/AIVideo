"""中文流水线环境：加载 .env（shared + zh 分块）+ logs/output/archive 目录。"""

from __future__ import annotations

import os
import re
from pathlib import Path

from paths import ROOT

_SECTION_RE = re.compile(r"^#==\s*section:\s*(\w+)\s*==")


def normalize_locale(_raw: str | None = None) -> str:
    """只保留中文流水线。"""
    return "zh"


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


def _apply_env_sections(path: Path, *, force_overlay: bool) -> None:
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
        elif section == "zh":
            if force_overlay or key not in os.environ:
                os.environ[key] = val


def load_locale_env(locale: str | None = None, *, force_overlay: bool = True) -> str:
    """加载 .env：shared + zh。忽略 locale 参数。"""
    del locale
    os.environ["AIVIDEO_LOCALE"] = "zh"
    _apply_env_sections(ROOT / ".env", force_overlay=force_overlay)
    return "zh"


def locale_logs_dir(locale: str | None = None) -> Path:
    del locale
    p = ROOT / "logs" / "zh"
    p.mkdir(parents=True, exist_ok=True)
    return p


def locale_output_dir(locale: str | None = None) -> Path:
    del locale
    p = ROOT / "output" / "zh"
    p.mkdir(parents=True, exist_ok=True)
    return p


def archive_published_dir(date_tag: str, locale: str | None = None) -> Path:
    del locale
    p = ROOT / "archive" / "published" / date_tag / "zh"
    p.mkdir(parents=True, exist_ok=True)
    return p


def host_intro_in_video() -> bool:
    """吉祥物自我介绍片头；默认开，AIVIDEO_HOST_INTRO=0 可关。"""
    raw = os.environ.get("AIVIDEO_HOST_INTRO", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def latest_output_video(locale: str | None = None) -> Path | None:
    del locale
    loc_dir = locale_output_dir()
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
