#!/usr/bin/env python3
"""PC WeChat client: publish to Channels via UI automation (Windows test).

Flow: Channels -> My profile -> Post video -> upload MP4 -> description -> publish.
Default: latest zh video under archive/published.

Deps (Windows)::
    pip install -r requirements-pcwechat.txt

Examples::
    python src/publish_shipinhao_pcwechat.py --latest
    python src/publish_shipinhao_pcwechat.py --skip-nav --no-publish
    python src/publish_shipinhao_pcwechat.py --probe
    python src/publish_shipinhao_pcwechat.py --dump-controls
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from paths import ROOT

if sys.platform != "win32":
    sys.exit("Windows only (PC WeChat UI automation)")

try:
    from pywinauto import Desktop
    from pywinauto.keyboard import send_keys
except ImportError as exc:
    sys.exit(
        "Missing pywinauto. Run: pip install -r requirements-pcwechat.txt\n"
        f"Detail: {exc}"
    )


class PcWeChatPublishError(Exception):
    pass


def _log(msg: str) -> None:
    print(msg, flush=True)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def find_latest_archive_video(locale: str = "zh") -> tuple[Path, Path | None]:
    base = ROOT / "archive" / "published"
    if not base.is_dir():
        raise PcWeChatPublishError(f"missing archive dir: {base}")

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
        raise PcWeChatPublishError(f"no {locale} videos under archive/published")
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
        _log(f"  [debug] screenshot failed: {exc}")
        return None


def _desktop_uia():
    return Desktop(backend="uia")


def iter_wechat_windows():
    desktop = _desktop_uia()
    seen: set[int] = set()
    for win in desktop.windows():
        try:
            handle = int(win.handle)
            if handle in seen:
                continue
            seen.add(handle)
            title = (win.window_text() or "").strip()
            cls = win.class_name() or ""
            exe = ""
            try:
                exe = (win.process_module() or "").lower()
            except Exception:
                pass
            if "wechat" in exe or "wechat" in cls.lower() or title == "微信":
                yield win, title, cls, exe
        except Exception:
            continue


def _channels_account_hint() -> str:
    return _env("WECHAT_CHANNELS_ACCOUNT") or _env("AIVIDEO_BRAND_NAME", "AI财知道")


def probe_windows() -> None:
    _log("=== WeChat-related top-level windows ===")
    for win, title, cls, exe in iter_wechat_windows():
        try:
            rect = win.rectangle()
            geo = f"{rect.width()}x{rect.height()}"
        except Exception:
            geo = "?"
        _log(f"  title={title!r}  class={cls!r}  exe={exe!r}  size={geo}")


def dump_controls(limit: int = 120) -> None:
    _log("=== Visible controls (text non-empty) ===")
    count = 0
    for win, wtitle, cls, exe in iter_wechat_windows():
        _log(f"-- window title={wtitle!r} class={cls!r} exe={exe!r}")
        try:
            for elem in win.descendants():
                if count >= limit:
                    _log(f"  ... truncated at {limit}")
                    return
                try:
                    if not elem.is_visible():
                        continue
                    name = (elem.window_text() or "").strip()
                    if not name:
                        continue
                    info = elem.element_info
                    _log(
                        f"  [{info.control_type}] {name!r} "
                        f"class={info.class_name!r}"
                    )
                    count += 1
                except Exception:
                    continue
        except Exception as exc:
            _log(f"  (descendants failed: {exc})")


def find_control_deep(*needles: str):
    needles = [n for n in needles if n]
    if not needles:
        return None, None

    for win, _, _, _ in iter_wechat_windows():
        for spec in (
            {"control_type": "Button"},
            {"control_type": "Hyperlink"},
            {"control_type": "Text"},
            {"control_type": "TabItem"},
            {"control_type": "ListItem"},
            {"control_type": "MenuItem"},
            {},
        ):
            for needle in needles:
                try:
                    if spec:
                        ctrl = win.child_window(title=needle, **spec)
                    else:
                        ctrl = win.child_window(title=needle)
                    if ctrl.exists(timeout=0.15):
                        return win, ctrl
                except Exception:
                    pass
                try:
                    if spec:
                        ctrl = win.child_window(title_re=f".*{needle}.*", **spec)
                    else:
                        ctrl = win.child_window(title_re=f".*{needle}.*")
                    if ctrl.exists(timeout=0.15):
                        return win, ctrl
                except Exception:
                    pass

        try:
            for elem in win.descendants():
                try:
                    if not elem.is_visible():
                        continue
                    name = (elem.window_text() or "").strip()
                    if not name:
                        continue
                    for needle in needles:
                        if needle in name:
                            return win, elem
                except Exception:
                    continue
        except Exception:
            continue
    return None, None


def find_main_wechat_window():
    best = None
    best_area = 0
    for win, title, cls, exe in iter_wechat_windows():
        if "wechat.exe" not in exe or "appex" in exe:
            continue
        try:
            rect = win.rectangle()
            area = rect.width() * rect.height()
            if area > best_area:
                best_area = area
                best = win
        except Exception:
            if title == "微信" or "WeChatMainWnd" in cls:
                return win
    return best


def _window_has_publish_form(win) -> bool:
    title = ""
    try:
        title = win.window_text() or ""
    except Exception:
        pass
    if any(k in title for k in ("发表动态", "视频管理")):
        return True
    probes = ("发表动态", "视频描述", "添加描述", "上传时长", "视频管理")
    for text in probes:
        try:
            if win.child_window(title_re=f".*{text}.*").exists(timeout=0.15):
                return True
        except Exception:
            pass
    try:
        for elem in win.descendants():
            try:
                if not elem.is_visible():
                    continue
                name = (elem.window_text() or "").strip()
                if name in probes or any(p in name for p in probes):
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def find_publish_window(*, timeout: float = 20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for win, _, _, _ in iter_wechat_windows():
            if _window_has_publish_form(win):
                try:
                    win.set_focus()
                except Exception:
                    pass
                return win
        time.sleep(0.4)
    return None


def _click_control(host, ctrl) -> None:
    try:
        host.set_focus()
    except Exception:
        pass
    try:
        ctrl.click_input()
        return
    except Exception:
        pass
    try:
        ctrl.invoke()
        return
    except Exception:
        pass
    rect = ctrl.rectangle()
    host.click_input(coords=(rect.mid_point().x, rect.mid_point().y))


def _click_sidebar_channels_icon(main_win) -> bool:
    try:
        main_win.set_focus()
        rect = main_win.rectangle()
    except Exception as exc:
        _log(f"  [nav] main window focus failed: {exc}")
        return False

    x = rect.left + int(_env("WECHAT_SIDEBAR_X", "35"))
    ratio_env = _env("WECHAT_SIDEBAR_CHANNELS_Y_RATIO", "")
    if ratio_env:
        ratios = [float(ratio_env)]
    else:
        ratios = [0.48, 0.54, 0.60, 0.66, 0.72]

    for ratio in ratios:
        y = rect.top + int(rect.height() * ratio)
        _log(f"  [nav] sidebar click ({x}, {y}) ratio={ratio:.2f}")
        try:
            main_win.click_input(coords=(x, y))
        except Exception:
            continue
        time.sleep(1.5)
        _, probe = find_control_deep("发表视频", "发起直播", _channels_account_hint())
        if probe:
            return True
    return False


def open_publish_page(*, skip_nav: bool = False) -> object:
    existing = find_publish_window(timeout=2.0 if skip_nav else 1.0)
    if existing:
        _log("  [nav] publish form already open")
        return existing

    if skip_nav:
        raise PcWeChatPublishError(
            "publish form not found (--skip-nav). "
            "Open PC WeChat: Channels -> My -> Post video, then retry."
        )

    account = _channels_account_hint()

    _log("  [nav] search Post video button (deep)...")
    host, btn = find_control_deep("发表视频")
    if btn:
        _log("  [nav] click Post video")
        _click_control(host, btn)
        found = find_publish_window(timeout=15.0)
        if found:
            return found

    _log("  [nav] open account tab if present...")
    if account:
        host, tab = find_control_deep(account)
        if tab:
            _log(f"  [nav] click account tab: {account!r}")
            _click_control(host, tab)
            time.sleep(1.2)
            host, btn = find_control_deep("发表视频")
            if btn:
                _click_control(host, btn)
                found = find_publish_window(timeout=15.0)
                if found:
                    return found

    main = find_main_wechat_window()
    if main:
        _log("  [nav] click Channels icon on main sidebar...")
        if _click_sidebar_channels_icon(main):
            if account:
                host, tab = find_control_deep(account)
                if tab:
                    _click_control(host, tab)
                    time.sleep(1.0)
            host, btn = find_control_deep("发表视频")
            if btn:
                _log("  [nav] click Post video after sidebar")
                _click_control(host, btn)
                found = find_publish_window(timeout=15.0)
                if found:
                    return found

    _log("  [nav] retry Post video on all windows...")
    for win, title, _, _ in iter_wechat_windows():
        try:
            btn = win.child_window(title="发表视频", control_type="Button")
            if btn.exists(timeout=0.3):
                _log(f"  [nav] click Post video in {title!r}")
                _click_control(win, btn)
                found = find_publish_window(timeout=12.0)
                if found:
                    return found
        except Exception:
            pass

    shot = _save_debug_shot("nav_fail")
    hint = f" screenshot: {shot}" if shot else ""
    raise PcWeChatPublishError(
        "cannot open publish form. Manual: PC WeChat -> Channels -> My -> Post video, "
        f"then run with --skip-nav.{hint} "
        "Tip: run --dump-controls to inspect UI tree; set WECHAT_SIDEBAR_CHANNELS_Y_RATIO "
        "if sidebar icon position differs."
    )


def _click_upload_area(win) -> None:
    host, label = find_control_deep("上传时长", "点击上传", "MP4", "H.264")
    if label:
        _click_control(host, label)
        return

    hints = ("上传时长", "点击上传", "上传视频", "MP4")
    for hint in hints:
        try:
            label = win.child_window(title_re=f".*{hint}.*")
            if label.exists(timeout=0.5):
                label.click_input()
                return
        except Exception:
            pass

    try:
        rect = win.rectangle()
        x = rect.left + int(rect.width() * 0.22)
        y = rect.top + int(rect.height() * 0.42)
        win.click_input(coords=(x, y))
    except Exception as exc:
        raise PcWeChatPublishError(f"cannot click upload area: {exc}") from exc


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
                            _log(f"  [upload] selected: {video.name}")
                            return
                    except Exception:
                        pass
                send_keys("{ENTER}")
                _log(f"  [upload] selected: {video.name}")
                return
            except Exception:
                pass
        time.sleep(0.25)

    raise PcWeChatPublishError("file open dialog timeout")


def upload_video(win, video: Path) -> None:
    if not video.is_file():
        raise PcWeChatPublishError(f"video not found: {video}")
    _log(f"  [upload] {video}")
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
    _log("  [upload] waiting for processing...")
    deadline = time.time() + timeout
    last_log = 0.0
    while time.time() < deadline:
        if _is_uploading(win):
            if time.time() - last_log > 8:
                _log("  [upload] still processing...")
                last_log = time.time()
            time.sleep(2.0)
            continue
        if _publish_button(win, click=False):
            _log("  [upload] ready")
            return
        time.sleep(2.0)
    raise PcWeChatPublishError("upload/processing timeout")


def fill_description(win, body: str) -> None:
    if not body.strip():
        raise PcWeChatPublishError("empty description")

    _log(f"  [form] description ({len(body)} chars)")
    host, edit = find_control_deep("添加描述")
    if edit:
        _click_control(host, edit)
        try:
            edit.set_edit_text(body)
        except Exception:
            send_keys("^a")
            send_keys(body, with_spaces=True)
        return

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
            if edit.exists(timeout=0.8):
                edit.click_input()
                try:
                    edit.set_edit_text(body)
                except Exception:
                    send_keys("^a")
                    send_keys(body, with_spaces=True)
                return
        except Exception:
            pass
    raise PcWeChatPublishError("description field not found")


def _publish_button(win, *, click: bool) -> bool:
    host, btn = find_control_deep("发表", "发布", "立即发表")
    if btn:
        try:
            if click:
                _click_control(host, btn)
            else:
                if btn.is_enabled():
                    return True
        except Exception:
            if click:
                _click_control(host, btn)
            else:
                return True
        return click

    for name in ("发表", "发布", "立即发表"):
        try:
            btn = win.child_window(title=name, control_type="Button")
            if btn.exists(timeout=0.4) and btn.is_enabled():
                if click:
                    btn.click_input()
                return True
        except Exception:
            pass
    return False


def click_publish(win) -> None:
    _log("  [publish] click publish...")
    if _publish_button(win, click=True):
        return
    raise PcWeChatPublishError("publish button not found")


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
        _log("  [publish] --no-publish: uploaded and filled, not submitted")
        return {"published": False, "video": str(video)}

    click_publish(win)
    time.sleep(2.0)
    _log("  [publish] clicked publish (confirm in WeChat UI)")
    return {"published": True, "video": str(video), "desc_len": len(body)}


def _write_result(payload: dict) -> None:
    path = _logs_dir() / "last_shipinhao_pcwechat_publish.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"  log: {path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PC WeChat Channels UI publish (test)")
    parser.add_argument("--video", type=Path, help="MP4 path (default: --latest)")
    parser.add_argument("--latest", action="store_true", help="latest archive zh video")
    parser.add_argument("--locale", default="zh", choices=("zh", "en"))
    parser.add_argument("--desc", default="", help="override description")
    parser.add_argument("--dry-run", action="store_true", help="resolve assets only")
    parser.add_argument("--no-publish", action="store_true", help="upload+fill, no submit")
    parser.add_argument("--skip-nav", action="store_true", help="publish form already open")
    parser.add_argument("--probe", action="store_true", help="list WeChat windows")
    parser.add_argument("--dump-controls", action="store_true", help="dump visible control names")
    args = parser.parse_args(argv)

    if args.probe:
        probe_windows()
        return 0
    if args.dump_controls:
        probe_windows()
        dump_controls()
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

    _log("== PC WeChat Channels publish (test) ==")
    _log(f"  video: {video}")
    _log(f"  body: {body[:120]}{'...' if len(body) > 120 else ''}")

    if args.dry_run:
        _log("  [dry-run] skipped WeChat UI")
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
            _log(f"  [error] screenshot: {shot}")
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
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
