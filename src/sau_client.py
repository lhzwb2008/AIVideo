"""social-auto-upload（sau CLI）— 仅用于登录与 cookie 校验。"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class SauError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def sau_home(root: Path | None = None) -> Path:
    from paths import ROOT

    root = root or ROOT
    custom = _env("SAU_HOME")
    if custom:
        return Path(custom).expanduser()
    return root / "vendor" / "social-auto-upload"


def _setup_hint() -> str:
    if os.name == "nt":
        return "请先运行: .\\setup-windows.ps1\n或设置 SAU_BIN / SAU_HOME，见 .env.example"
    return "请先运行: .\\setup-windows.ps1\n或设置 SAU_BIN / SAU_HOME，见 .env.example"


def is_sau_config_error(message: str) -> bool:
    """环境/配置类错误，重试无意义。"""
    markers = (
        "未找到 sau",
        "SAU_BIN 不存在",
        "SAU_HOME 目录不存在",
        "未安装 patchright",
    )
    return any(m in (message or "") for m in markers)


def resolve_sau_bin(root: Path | None = None) -> Path:
    explicit = _env("SAU_BIN")
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise SauError(f"SAU_BIN 不存在: {p}")
        return p

    from sau_paths import sau_bin

    home = sau_home(root)
    found = sau_bin(home)
    if found:
        return found

    which = shutil.which("sau")
    if which:
        p = Path(which)
        if p.is_file():
            return p

    raise SauError(f"未找到 sau 命令。{_setup_hint()}")


def douyin_account() -> str:
    return _env("SAU_DOUYIN_ACCOUNT", "main")


def bilibili_account() -> str:
    return _env("SAU_BILIBILI_ACCOUNT", "main")


def bilibili_cookie_path(*, root: Path | None = None) -> Path:
    return sau_home(root) / "cookies" / f"bilibili_{bilibili_account()}.json"


def run_sau(
    args: list[str],
    *,
    root: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    bin_path = resolve_sau_bin(root)
    home = sau_home(root)
    if not home.is_dir():
        raise SauError(f"SAU_HOME 目录不存在: {home}\n{_setup_hint()}")

    env = os.environ.copy()
    env.setdefault("SAU_HOME", str(home))

    cmd = [str(bin_path), *args]
    proc = subprocess.run(
        cmd,
        cwd=home,
        env=env,
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise SauError(f"sau 失败 (exit {proc.returncode}): {' '.join(cmd)}\n{detail}")
    return proc


def check_douyin_session(*, root: Path | None = None) -> None:
    proc = run_sau(
        ["douyin", "check", "--account", douyin_account()],
        root=root,
        check=False,
    )
    out = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode == 0 and "valid" in out.lower():
        return
    raise SauError(
        f"抖音 cookie 无效。请运行: .\\scripts\\login-cn.ps1 douyin --force\n{out or f'exit {proc.returncode}'}"
    )


def check_bilibili_session(*, root: Path | None = None) -> None:
    cookie = bilibili_cookie_path(root=root)
    if not cookie.is_file():
        raise SauError(
            f"B 站账号文件不存在: {cookie}\n请先运行: .\\scripts\\login-cn.ps1 bilibili"
        )
    proc = run_sau(
        ["bilibili", "check", "--account", bilibili_account()],
        root=root,
        check=False,
    )
    out = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode == 0 and "valid" in out.lower():
        return
    raise SauError(
        f"B 站登录态无效。请运行: .\\scripts\\login-cn.ps1 bilibili\n{out or f'exit {proc.returncode}'}"
    )


def bilibili_video_upload_skippable(message: str) -> bool:
    """视频已在站内成功或触发频控时，不必再跑 biliup 上传。"""
    m = message or ""
    return "21566" in m or "投稿过于频繁" in m


def publish_bilibili_video(
    video: Path,
    *,
    title: str,
    desc: str,
    tags: str,
    tid: int,
    root: Path | None = None,
) -> str:
    """通过 sau + biliup 上传视频，成功返回标题（作发布记录）。"""
    args = [
        "bilibili",
        "upload-video",
        "--account",
        bilibili_account(),
        "--file",
        str(video.resolve()),
        "--title",
        title,
        "--desc",
        desc,
        "--tid",
        str(tid),
    ]
    if tags.strip():
        args.extend(["--tags", tags])
    proc = run_sau(args, root=root, check=False)
    out = (proc.stdout or proc.stderr or "").strip()
    if proc.returncode != 0:
        raise SauError(
            f"B 站上传失败 (exit {proc.returncode}):\n{out or '无输出'}"
        )
    return title
