#!/usr/bin/env python3
"""B站创作中心扫码登录（biliup），等同 bilibili-login.sh。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from paths import ROOT
from sau_client import bilibili_account, sau_home


def _cookie_path(*, root: Path, account: str) -> Path:
    return sau_home(root) / "cookies" / f"bilibili_{account}.json"


def _import_runtime():
    from apply_sau_patches import BILIUP_RUNTIME, patch_biliup_runtime

    patch_biliup_runtime(BILIUP_RUNTIME)
    home = str(sau_home(ROOT))
    if home not in sys.path:
        sys.path.insert(0, home)
    from uploader.bilibili_uploader.runtime import (
        build_biliup_runtime_path,
        ensure_biliup_binary,
        run_biliup_command,
    )

    return ensure_biliup_binary, run_biliup_command, build_biliup_runtime_path


def _session_valid(account_file: Path, run_biliup_command) -> bool:
    if not account_file.is_file():
        return False
    result = run_biliup_command(["-u", str(account_file), "renew"])
    return result.returncode == 0


def _ensure_biliup(ensure_biliup_binary, build_biliup_runtime_path) -> Path:
    path = build_biliup_runtime_path()
    if path.is_file():
        print(f"biliup 已就绪: {path}", flush=True)
        return ensure_biliup_binary(force_check=False)
    print("首次使用：正在下载 biliup（约 1-3 分钟，请稍候）…", flush=True)
    gh = os.environ.get("GH_PROXY", "").strip()
    if gh:
        print(f"使用 GitHub 镜像: {gh}", flush=True)
    else:
        print("若长时间无响应，可在 .env 添加: GH_PROXY=https://ghproxy.net", flush=True)
    binary = ensure_biliup_binary(force_check=True)
    print(f"下载完成: {binary}", flush=True)
    return binary


def _run_login(account_file: Path, run_biliup_command) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "B站登录需在交互式终端运行（RDP 桌面 PowerShell）。\n"
            "若终端二维码不完整，请在 vendor\\social-auto-upload 目录打开 qrcode.png 扫码。",
            file=sys.stderr,
        )
        return 1
    print("", flush=True)
    print("即将打开 biliup 扫码登录，请用 B 站 App 扫描终端二维码。", flush=True)
    print("终端二维码不完整时，请打开 vendor\\social-auto-upload\\qrcode.png", flush=True)
    print("创作中心: https://member.bilibili.com/platform/home", flush=True)
    print("", flush=True)
    os.chdir(sau_home(ROOT))
    result = run_biliup_command(["-u", str(account_file), "login"], interactive=True)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="B站 biliup 扫码登录")
    parser.add_argument("--login", action="store_true", help="扫码登录/续期")
    parser.add_argument("--check", action="store_true", help="只校验登录态")
    parser.add_argument("--force", action="store_true", help="删除旧 cookie 后重新登录")
    parser.add_argument("--account", default="", help="账号名（默认 SAU_BILIBILI_ACCOUNT / main）")
    args = parser.parse_args()

    account = args.account or bilibili_account()
    cookie = _cookie_path(root=ROOT, account=account)
    cookie.parent.mkdir(parents=True, exist_ok=True)

    ensure_biliup_binary, run_biliup_command, build_biliup_runtime_path = _import_runtime()
    _ensure_biliup(ensure_biliup_binary, build_biliup_runtime_path)

    print(f"账号: {account}", flush=True)
    print(f"账号文件: {cookie}", flush=True)

    if args.check:
        if _session_valid(cookie, run_biliup_command):
            print("B 站登录态有效")
            return 0
        print("B 站登录态无效", file=sys.stderr)
        return 1

    if not args.login:
        parser.print_help()
        return 2

    if args.force and cookie.is_file():
        print("强制重新登录：删除旧账号文件…", flush=True)
        cookie.unlink()

    if not args.force and cookie.is_file() and _session_valid(cookie, run_biliup_command):
        print("无需重新登录。若上传仍失败: .\\scripts\\login-cn.ps1 bilibili -Force")
        return 0

    code = _run_login(cookie, run_biliup_command)
    if code != 0:
        print(f"biliup login 失败 (exit {code})", file=sys.stderr)
        return code

    print("", flush=True)
    print("登录流程结束，正在校验…", flush=True)
    if _session_valid(cookie, run_biliup_command):
        print("验证通过。可在 .env 设 AIVIDEO_PUBLISH_BILIBILI=1")
        return 0
    print(
        "登录后校验仍未通过，请重试: .\\scripts\\login-cn.ps1 bilibili -Force",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
