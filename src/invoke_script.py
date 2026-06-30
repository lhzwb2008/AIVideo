"""跨平台调用 scripts/*.sh（Windows 直调 Python 等价入口）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from paths import ROOT

_SCRIPT_MAP: dict[str, tuple[str, bool]] = {
    "run-enrich-images": ("enrich_images.py", False),
    "run-compose": ("video_compose.py", False),
    "publish-bilibili": ("publish_bilibili.py", True),
    "publish-eastmoney": ("publish_eastmoney.py", True),
    "publish-xueqiu": ("publish_xueqiu.py", True),
    "publish-wechat": ("publish_wechat.py", True),
    "publish-zhihu": ("publish_zhihu.py", True),
    "publish-llm-browser": ("llm_browser_publish.py", True),
    "publish-youtube": ("publish_youtube.py", False),
    "publish-tiktok": ("publish_tiktok.py", False),
    "publish-shipinhao": ("social_publisher.py", True),
    "publish-shipinhao-pcwechat": ("publish_shipinhao_pcwechat.py", False),
}


def main_python() -> Path:
    for candidate in (
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python3",
        ROOT / ".venv" / "bin" / "python",
    ):
        if candidate.is_file():
            return candidate
    return Path(sys.executable)


def sau_python() -> Path:
    from sau_paths import sau_python as _sau_py

    sau_home = Path(
        os.environ.get("SAU_HOME", str(ROOT / "vendor" / "social-auto-upload"))
    ).expanduser()
    found = _sau_py(sau_home)
    if found:
        return found
    return main_python()


def script_argv(stem: str, *args: str | Path) -> list[str]:
    parts = [str(a) for a in args]
    if sys.platform == "win32" and stem in _SCRIPT_MAP:
        module, use_sau = _SCRIPT_MAP[stem]
        py = sau_python() if use_sau else main_python()
        return [str(py), str(ROOT / "src" / module), *parts]
    return [str(ROOT / "scripts" / f"{stem}.sh"), *parts]
