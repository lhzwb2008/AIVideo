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


def resolve_sau_bin(root: Path | None = None) -> Path:
    explicit = _env("SAU_BIN")
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            raise SauError(f"SAU_BIN 不存在: {p}")
        return p

    home = sau_home(root)
    candidates: list[Path] = [
        home / ".venv" / "bin" / "sau",
        home / "venv" / "bin" / "sau",
    ]
    which = shutil.which("sau")
    if which:
        candidates.append(Path(which))
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    raise SauError(
        "未找到 sau 命令。请先运行: ./setup-sau.sh\n"
        "或设置 SAU_BIN / SAU_HOME，见 .env.example"
    )


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
        raise SauError(f"SAU_HOME 目录不存在: {home}\n请运行 ./setup-sau.sh")

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
        f"抖音 cookie 无效。请运行: ./douyin-login.sh\n{out or f'exit {proc.returncode}'}"
    )


def check_bilibili_session(*, root: Path | None = None) -> None:
    cookie = bilibili_cookie_path(root=root)
    if not cookie.is_file():
        raise SauError(
            f"B 站账号文件不存在: {cookie}\n请先运行: ./bilibili-login.sh"
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
        f"B 站登录态无效。请运行: ./bilibili-login.sh\n{out or f'exit {proc.returncode}'}"
    )


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
