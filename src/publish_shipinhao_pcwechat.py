#!/usr/bin/env python3
"""PC 微信客户端 · 视频号 UI 自动化发布（Windows 测试）。

流程：视频号 → 我的 → 发表视频 → 上传 MP4 → 填描述 → 发表。
默认发布 archive/published 下最新的 zh 视频。

依赖（Windows）::
    pip install -r requirements-pcwechat.txt

用法::
    python src/publish_shipinhao_pcwechat.py --latest
    python src/publish_shipinhao_pcwechat.py --video archive/published/.../xxx.mp4
    python src/publish_shipinhao_pcwechat.py --probe          # 列出微信相关窗口
    python src/publish_shipinhao_pcwechat.py --latest --dry-run
    python src/publish_shipinhao_pcwechat.py --latest --no-publish  # 上传+填表不点发表
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

from paths import ROOT

if sys.platform != "win32":
    sys.exit("此脚本仅支持 Windows（PC 微信 UI 自动化）")

try:
    from pywinauto import Desktop
    from pywinauto.keyboard import send_keys
except ImportError as exc:
    sys.exit(
        "缺少 pywinauto，请安装: pip install -r requirements-pcwechat.txt\n"
        f"详情: {exc}"
    )


class PcWeChatPublishError(Exception):
    pass


def _log(msg: str) -> None:
    print(msg, flush=True)


def find_latest_archive_video(locale: str = "zh") -> tuple[Path, Path | None]:
    base = ROOT / "archive" / "published"
    if not base.is_dir():
        raise PcWeChatPublishError(f"不存在 archive 目录: {base}")

    best: Path | None = None
    best_mtime = 0.0
    best_pack: Path | None = None
    for mp4 in base.glob(f"**/{locale}/*.mp4"):
        m = mp4.stat().st_mtime
        if m > best_mtime:
            best_mtime = m
            best = mp4.resolve()
            pack = mp4.parent / mp4.stem
            best_pack = pack.resolve() if pack.is_dir() else None

    if not best:
        raise PcWeChatPublishError(f"archive/published 下没有 {locale} 视频")
    return best, best_pack


def _resolve_script_path(pack_dir: Path | None, video: Path) -> Path | None:
    if pack_dir:
        for name in ("script.json", "last_script.json"):
            candidate = pack_dir / name
            if candidate.is_file():
                return candidate
    from publish_resolve import resolve_script_for_video

    return resolve_script_for_video(video, None)


def _parse_archive_readme(readme: Path) -> dict[str, str]:
    import re

    text = readme.read_text(encoding="utf-8")
    out: dict[str, str] = {"title": "", "desc": "", "tags": ""}

    def _block(label: str) -> str:
        m = re.search(
            rf"\*\*{re.escape(label)}\*\*\s*\n\s*```\s*\n(.*?)\n```",
            text,
            re.S,
        )
        return m.group(1).strip() if m else ""

    out["title"] = _block("标题")
    out["tags"] = _block("标签")
    out["desc"] = _block("简介 + 话题") or _block("简介")
    return out


def resolve_publish_fields(
    video: Path,
    *,
    pack_dir: Path | None,
    desc_override: str = "",
) -> dict[str, str]:
    from douyin_caption import _strip_urls
    from publish_resolve import load_script
    from social_caption import build_social_fields

    script_path = _resolve_script_path(pack_dir, video)
    script = load_script(script_path)
    if script:
        fields = build_social_fields(script, "tencent")
        return {
            "title": fields["title"],
            "desc": _strip_urls(desc_override or fields["desc"]),
            "tags": ",".join(str(t) for t in (fields.get("tags") or [])),
            "short_title": fields.get("short_title") or "",
        }

    if pack_dir:
        readme = pack_dir / "README.md"
        if readme.is_file():
            parsed = _parse_archive_readme(readme)
            return {
                "title": parsed["title"] or video.stem,
                "desc": _strip_urls(desc_override or parsed["desc"]),
                "tags": parsed["tags"],
                "short_title": (parsed["title"] or video.stem)[:16],
            }

    return {
        "title": video.stem,
        "desc": _strip_urls(desc_override),
        "tags": "",
        "short_title": video.stem[:16],
    }


def build_publish_body(fields: dict) -> str:
    raw_tags = fields.get("tags") or ""
    if isinstance(raw_tags, list):
        tags = [str(t).strip().lstrip("#") for t in raw_tags if str(t).strip()]
    else:
        tags = [t.strip().lstrip("#") for t in str(raw_tags).split(",") if t.strip()]
    tag_line = " ".join(f"#{t}" for t in tags[:5])
    desc = str(fields.get("desc") or "").strip()
    if tag_line and tag_line not in desc:
        return f"{desc}\n{tag_line}".strip()
    return desc[:1000]


def _logs_dir() -> Path:
    d = ROOT / "logs" / "zh"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_debug_shot(tag: str) -> Path | None:
    try:
        from PIL import ImageGrab

        path = _logs_dir() / f"shipinhao_pcwechat_{tag}_{datetime.now():%Y%m%d_%H%M%S}.png"
        ImageGrab.grab().save(path)
        return path
    except Exception as exc:
        _log(f"  [debug] 截图失败: {exc}")
        return None


def _desktop_uia():
    return Desktop(backend="uia")


def iter_wechat_windows():
    desktop = _desktop_uia()
    for win in desktop.windows():
        try:
            title = (win.window_text() or "").strip()
            cls = win.class_name() or ""
            pid_name = ""
            try:
                pid_name = win.process_module() or ""
            except Exception:
                pass
            if not title and "WeChat" not in cls and "WeChat" not in pid_name:
                continue
            if any(
                key in title
                for key in (
                    "微信",
                    "WeChat",
                    "视频",
                    "发表",
                    "Channels",
                    "Finder",
                )
            ) or "WeChat" in pid_name:
                yield win, title, cls, pid_name
        except Exception:
            continue


def probe_windows() -> None:
    _log("=== PC 微信相关窗口 ===")
    for win, title, cls, exe in iter_wechat_windows():
        try:
            rect = win.rectangle()
            geo = f"{rect.width()}x{rect.height()}"
        except Exception:
            geo = "?"
        _log(f"  title={title!r}  class={cls!r}  exe={exe!r}  size={geo}")


def _window_has_publish_form(win) -> bool:
    title = ""
    try:
        title = win.window_text() or ""
    except Exception:
        pass
    if any(k in title for k in ("发表动态", "视频管理")):
        return True
    probes = ("发表动态", "视频描述", "添加描述", "上传时长")
    for text in probes:
        try:
            if win.child_window(title_re=f".*{text}.*").exists(timeout=0.2):
                return True
        except Exception:
            pass
    return False


def find_publish_window(*, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for win, title, _, _ in iter_wechat_windows():
            if _window_has_publish_form(win):
                try:
                    win.set_focus()
                except Exception:
                    pass
                return win
        time.sleep(0.4)
    return None


def _click_first_button(win, names: tuple[str, ...]) -> bool:
    for name in names:
        try:
            btn = win.child_window(title=name, control_type="Button")
            if btn.exists(timeout=0.8):
                btn.click_input()
                return True
        except Exception:
            pass
        try:
            btn = win.child_window(title_re=f"^{name}$", control_type="Hyperlink")
            if btn.exists(timeout=0.3):
                btn.click_input()
                return True
        except Exception:
            pass
    return False


def open_publish_page(*, skip_nav: bool = False) -> object:
    existing = find_publish_window(timeout=1.5 if skip_nav else 0.8)
    if existing:
        _log("  [nav] 已在发表动态页")
        return existing

    if skip_nav:
        raise PcWeChatPublishError(
            "未找到发表动态页（--skip-nav）。请先手动打开：视频号 → 我的 → 发表视频"
        )

    _log("  [nav] 查找「发表视频」入口…")
    for win, title, _, _ in iter_wechat_windows():
        if _click_first_button(win, ("发表视频",)):
            _log(f"  [nav] 已点击发表视频（窗口: {title!r}）")
            found = find_publish_window(timeout=15.0)
            if found:
                return found

    _log("  [nav] 尝试点击侧栏「视频号」…")
    for win, title, _, exe in iter_wechat_windows():
        if "WeChat.exe" not in exe and "微信" not in title:
            continue
        try:
            win.set_focus()
        except Exception:
            pass
        for spec in (
            dict(title="视频号", control_type="Button"),
            dict(title="视频号", control_type="ListItem"),
            dict(title_re=".*视频号.*", control_type="Text"),
        ):
            try:
                item = win.child_window(**spec)
                if item.exists(timeout=0.6):
                    item.click_input()
                    time.sleep(1.5)
                    break
            except Exception:
                pass
        if _click_first_button(win, ("发表视频",)):
            found = find_publish_window(timeout=15.0)
            if found:
                return found

    for win, _, _, _ in iter_wechat_windows():
        if _click_first_button(win, ("发表视频",)):
            found = find_publish_window(timeout=15.0)
            if found:
                return found

    shot = _save_debug_shot("nav_fail")
    hint = f" 已截图: {shot}" if shot else ""
    raise PcWeChatPublishError(
        "无法打开发表动态页。请手动打开：PC 微信 → 视频号 → 我的 → 发表视频，"
        f"然后重试 --skip-nav{hint}"
    )


def _click_upload_area(win) -> None:
    hints = ("上传时长", "点击上传", "上传视频", "MP4")
    for hint in hints:
        try:
            label = win.child_window(title_re=f".*{hint}.*")
            if label.exists(timeout=0.8):
                label.click_input()
                return
        except Exception:
            pass

    try:
        rect = win.rectangle()
        x = rect.left + int(rect.width() * 0.22)
        y = rect.top + int(rect.height() * 0.42)
        win.click_input(coords=(x, y))
        return
    except Exception as exc:
        raise PcWeChatPublishError(f"无法点击上传区域: {exc}") from exc


def _pick_file_in_dialog(video: Path, *, timeout: float = 30.0) -> None:
    desktop = _desktop_uia()
    path_str = str(video.resolve())
    deadline = time.time() + timeout

    while time.time() < deadline:
        for dlg_title in ("打开", "Open", "选择文件", "选择要上传的文件"):
            try:
                dlg = desktop.window(class_name="#32770", title=dlg_title)
                if not dlg.exists(timeout=0.2):
                    continue
                dlg.set_focus()
                edits = [e for e in dlg.descendants(control_type="Edit") if e.is_visible()]
                if edits:
                    edits[-1].set_focus()
                    try:
                        edits[-1].set_edit_text(path_str)
                    except Exception:
                        send_keys("^a")
                        send_keys(path_str, with_spaces=True)
                else:
                    send_keys("^a")
                    send_keys(path_str, with_spaces=True)
                time.sleep(0.2)
                for open_label in ("打开(&O)", "打开", "Open", "选择"):
                    try:
                        btn = dlg.child_window(title=open_label, control_type="Button")
                        if btn.exists(timeout=0.2):
                            btn.click_input()
                            _log(f"  [upload] 已选择: {video.name}")
                            return
                    except Exception:
                        pass
                send_keys("{ENTER}")
                _log(f"  [upload] 已选择: {video.name}")
                return
            except Exception:
                pass
        time.sleep(0.25)

    raise PcWeChatPublishError("文件选择对话框未出现（超时）")


def upload_video(win, video: Path) -> None:
    if not video.is_file():
        raise PcWeChatPublishError(f"视频不存在: {video}")
    _log(f"  [upload] 上传 {video}")
    _click_upload_area(win)
    time.sleep(0.8)
    _pick_file_in_dialog(video)


def _is_uploading(win) -> bool:
    busy_words = ("上传中", "处理中", "正在上传", "正在处理", "转码")
    for word in busy_words:
        try:
            if win.child_window(title_re=f".*{word}.*").exists(timeout=0.15):
                return True
        except Exception:
            pass
    return False


def wait_upload_done(win, *, timeout: float = 600.0) -> None:
    _log("  [upload] 等待视频处理…")
    deadline = time.time() + timeout
    last_log = 0.0
    while time.time() < deadline:
        if _is_uploading(win):
            if time.time() - last_log > 8:
                _log("  [upload] 仍在处理…")
                last_log = time.time()
            time.sleep(2.0)
            continue
        if _publish_button(win, click=False):
            _log("  [upload] 视频已就绪")
            return
        time.sleep(2.0)
    raise PcWeChatPublishError("视频上传/处理超时")


def fill_description(win, body: str) -> None:
    if not body.strip():
        raise PcWeChatPublishError("描述为空")
    _log(f"  [form] 填写描述 ({len(body)} 字)")
    specs = (
        dict(title="添加描述", control_type="Edit"),
        dict(title_re=".*添加描述.*", control_type="Edit"),
        dict(title_re=".*视频描述.*", control_type="Edit"),
        dict(control_type="Document"),
        dict(control_type="Edit"),
    )
    for spec in specs:
        try:
            edit = win.child_window(**spec)
            if edit.exists(timeout=1.0):
                edit.click_input()
                try:
                    edit.set_edit_text(body)
                except Exception:
                    send_keys("^a")
                    send_keys(body, with_spaces=True)
                return
        except Exception:
            pass
    raise PcWeChatPublishError("未找到「视频描述」输入框")


def _publish_button(win, *, click: bool) -> bool:
    for name in ("发表", "发布", "立即发表"):
        try:
            btn = win.child_window(title=name, control_type="Button")
            if btn.exists(timeout=0.5) and btn.is_enabled():
                if click:
                    btn.click_input()
                return True
        except Exception:
            pass
    return False


def click_publish(win) -> None:
    _log("  [publish] 点击发表…")
    if _publish_button(win, click=True):
        return
    raise PcWeChatPublishError("未找到可点击的「发表」按钮")


def publish_via_pc_wechat(
    video: Path,
    body: str,
    *,
    skip_nav: bool = False,
    no_publish: bool = False,
) -> dict:
    win = open_publish_page(skip_nav=skip_nav)
    upload_video(win, video)
    wait_upload_done(win)
    fill_description(win, body)
    if no_publish:
        _log("  [publish] --no-publish：已上传并填表，未点击发表")
        return {"published": False, "video": str(video)}

    click_publish(win)
    time.sleep(2.0)
    _log("  [publish] 已点击发表（请在微信内确认是否成功）")
    return {"published": True, "video": str(video), "desc_len": len(body)}


def _write_result(payload: dict) -> None:
    path = _logs_dir() / "last_shipinhao_pcwechat_publish.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"  日志: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PC 微信 · 视频号 UI 自动化发布（测试）")
    parser.add_argument("--video", type=Path, help="MP4 路径（默认 --latest）")
    parser.add_argument("--latest", action="store_true", help="发布 archive 最新 zh 视频（默认）")
    parser.add_argument("--locale", default="zh", choices=("zh", "en"))
    parser.add_argument("--desc", default="", help="覆盖描述（默认从 script/README 解析）")
    parser.add_argument("--dry-run", action="store_true", help="只解析素材，不操作微信")
    parser.add_argument("--no-publish", action="store_true", help="上传+填表，不点发表")
    parser.add_argument(
        "--skip-nav",
        action="store_true",
        help="跳过导航（发表动态页已手动打开时使用）",
    )
    parser.add_argument("--probe", action="store_true", help="列出微信相关窗口并退出")
    args = parser.parse_args(argv)

    if args.probe:
        probe_windows()
        return 0

    use_latest = args.latest or args.video is None
    if use_latest:
        video, pack_dir = find_latest_archive_video(args.locale)
    else:
        video = args.video if args.video.is_absolute() else (ROOT / args.video)
        video = video.resolve()
        pack = video.parent / video.stem
        pack_dir = pack if pack.is_dir() else None

    fields = resolve_publish_fields(video, pack_dir=pack_dir, desc_override=args.desc)
    body = build_publish_body(fields)

    _log("== PC 微信 · 视频号发布（测试） ==")
    _log(f"  视频: {video}")
    _log(f"  描述: {body[:120]}{'…' if len(body) > 120 else ''}")

    if args.dry_run:
        _log("  [dry-run] 未操作微信")
        return 0

    try:
        result = publish_via_pc_wechat(
            video,
            body,
            skip_nav=args.skip_nav,
            no_publish=args.no_publish,
        )
        result["platform"] = "shipinhao_pcwechat"
        result["body_preview"] = body[:200]
        result["ts"] = datetime.now().isoformat(timespec="seconds")
        _write_result(result)
        return 0
    except Exception as exc:
        shot = _save_debug_shot("error")
        if shot:
            _log(f"  [error] 截图: {shot}")
        _write_result(
            {
                "published": False,
                "error": str(exc),
                "video": str(video),
                "ts": datetime.now().isoformat(timespec="seconds"),
            }
        )
        raise


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PcWeChatPublishError as exc:
        print(f"错误: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
