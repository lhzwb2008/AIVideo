#!/usr/bin/env python3
"""PC WeChat client: publish to Channels via UI automation (Windows test).

Default start page: Channels home feed (视频号首页) after daily publish.
Flow: home -> My profile -> Post video -> upload MP4 -> description -> publish.

Deps (Windows)::
    pip install -r requirements-pcwechat.txt

Examples::
    python src/publish_shipinhao_pcwechat.py --latest
    python src/publish_shipinhao_pcwechat.py --no-publish
    python src/publish_shipinhao_pcwechat.py --skip-nav   # publish form already open
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
    from pywinauto import mouse
    from pywinauto.keyboard import send_keys
except ImportError as exc:
    sys.exit(
        "Missing pywinauto. Run: pip install -r requirements-pcwechat.txt\n"
        f"Detail: {exc}"
    )


class PcWeChatPublishError(Exception):
    pass


class PublishSession:
    def __init__(self, window, *, sparse: bool = False) -> None:
        self.window = window
        self.sparse = sparse


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


def get_foreground_wechat_window():
    try:
        import win32gui

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        win = _desktop_uia().window(handle=hwnd)
        exe = (win.process_module() or "").lower()
        if "wechat" in exe:
            return win
    except Exception:
        pass
    return None


def _win32_window_text(hwnd: int) -> str:
    try:
        import win32gui

        return (win32gui.GetWindowText(hwnd) or "").strip()
    except Exception:
        return ""


def _window_geometry(win) -> tuple[int, int, int, int] | None:
    try:
        import win32gui

        left, top, right, bottom = win32gui.GetWindowRect(int(win.handle))
        return left, top, right - left, bottom - top
    except Exception:
        pass
    try:
        rect = win.rectangle()
        return rect.left, rect.top, rect.width(), rect.height()
    except Exception:
        return None


def _screen_click(x: int, y: int, *, double: bool = False) -> None:
    if double:
        mouse.double_click(button="left", coords=(x, y))
    else:
        mouse.click(button="left", coords=(x, y))


def _focus_hwnd(hwnd: int) -> None:
    try:
        import win32con
        import win32gui

        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass


def _force_foreground(hwnd: int) -> None:
    try:
        import win32con
        import win32gui
        import win32process

        if win32gui.GetForegroundWindow() == hwnd:
            return
        fg = win32gui.GetForegroundWindow()
        fg_thread = win32process.GetWindowThreadProcessId(fg)[0]
        target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
        attached = False
        if fg_thread and target_thread and fg_thread != target_thread:
            win32process.AttachThreadInput(fg_thread, target_thread, True)
            attached = True
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
            win32gui.SetForegroundWindow(hwnd)
        finally:
            if attached:
                win32process.AttachThreadInput(fg_thread, target_thread, False)
    except Exception:
        _focus_hwnd(hwnd)


def _is_wechat_foreground(win) -> bool:
    try:
        import win32gui
        import win32process

        hwnd = int(win.handle)
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            return True
        _, fg_pid = win32process.GetWindowThreadProcessId(fg)
        _, win_pid = win32process.GetWindowThreadProcessId(hwnd)
        return fg_pid == win_pid
    except Exception:
        return False


def _prepare_wechat_foreground(win, *, retries: int = 5) -> bool:
    hwnd = int(win.handle)
    for attempt in range(retries):
        _force_foreground(hwnd)
        time.sleep(0.35 + attempt * 0.12)
        if _is_wechat_foreground(win):
            return True
    _log("  [nav] warning: WeChat not foreground — close/minimize PowerShell and retry")
    return False


def _window_large_enough(win, *, min_w: int = 480, min_h: int = 360) -> bool:
    geo = _window_geometry(win)
    if not geo:
        return False
    _, _, w, h = geo
    return w >= min_w and h >= min_h


def find_largest_wechat_window(*, prefer_appex: bool = False):
    best = None
    best_area = 0
    for win, _, _, exe in iter_wechat_windows():
        if prefer_appex and "appex" not in exe:
            continue
        geo = _window_geometry(win)
        if not geo:
            continue
        _, _, w, h = geo
        if w < 480 or h < 360:
            continue
        area = w * h
        if area > best_area:
            best_area = area
            best = win
    return best


def _rect_click(win, x_ratio: float, y_ratio: float, *, double: bool = False) -> None:
    geo = _window_geometry(win)
    if not geo:
        raise PcWeChatPublishError("cannot read window geometry")
    left, top, w, h = geo
    x = left + int(w * x_ratio)
    y = top + int(h * y_ratio)
    _focus_hwnd(int(win.handle))
    time.sleep(0.15)
    _log(f"  [click] screen ({x},{y}) ratio=({x_ratio:.2f},{y_ratio:.2f}) win={w}x{h}")
    _screen_click(x, y, double=double)


def _set_clipboard_text(text: str) -> None:
    import win32clipboard
    import win32con

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def _ratio_env(name: str, default: float) -> float:
    raw = _env(name, "")
    if not raw:
        return default
    return float(raw)


def _channels_account_hint() -> str:
    return _env("WECHAT_CHANNELS_ACCOUNT") or _env("AIVIDEO_BRAND_NAME", "AI财知道")


def _sidebar_channels_y_ratios() -> list[float]:
    ratio_env = _env("WECHAT_SIDEBAR_CHANNELS_Y_RATIO", "")
    if ratio_env:
        return [float(ratio_env)]
    return [0.48, 0.54, 0.60, 0.66, 0.72]


def _channels_my_tab_points() -> list[tuple[float, float]]:
    """PC WeChat Channels: profile is top account tab or top-right avatar (not bottom 我)."""
    custom_x = _env("WECHAT_CHANNELS_MY_X_RATIO", "")
    custom_y = _env("WECHAT_CHANNELS_MY_Y_RATIO", "")
    if custom_x and custom_y:
        return [(float(custom_x), float(custom_y))]
    return [
        (0.58, 0.05),
        (0.94, 0.05),
        (0.52, 0.05),
        (0.96, 0.06),
        (0.55, 0.04),
    ]


def _channels_post_video_points() -> list[tuple[float, float]]:
    custom_x = _env("WECHAT_POST_VIDEO_X_RATIO", "")
    custom_y = _env("WECHAT_POST_VIDEO_Y_RATIO", "")
    if custom_x and custom_y:
        return [(float(custom_x), float(custom_y))]
    return [
        (0.42, 0.28),
        (0.38, 0.26),
        (0.45, 0.30),
        (0.40, 0.32),
    ]


_VISION_NAV_SYSTEM = """You locate ONE click point on a Windows PC WeChat Channels (视频号) screenshot.
Return ONLY JSON:
{"x":0.0-1.0,"y":0.0-1.0,"label":"element","confidence":"high|low","found":true}

Rules:
- x,y normalized to the FULL screenshot (0=left/top, 1=right/bottom).
- Layout: narrow WeChat icon sidebar on the left; Channels panel on the right.
- On the home feed, account tab (creator name) and profile icon are at the TOP — not bottom.
- NEVER click the large center video playback area when opening profile or Post video.
- If the target is not visible, return {"found":false,"label":"reason"}."""


def _vision_nav_enabled() -> bool:
    raw = _env("WECHAT_VISION_NAV", "auto").lower()
    if raw in ("0", "false", "no", "off"):
        return False
    try:
        from llm_vision_client import llm_vision_available

        available = llm_vision_available()
    except Exception:
        return False
    if raw in ("1", "true", "yes", "on"):
        return available
    return available


def _screenshot_window(win) -> Path | None:
    geo = _window_geometry(win)
    if not geo:
        return None
    try:
        from PIL import ImageGrab

        left, top, w, h = geo
        img = ImageGrab.grab(bbox=(left, top, left + w, top + h))
        path = _logs_dir() / f"shipinhao_pcwechat_win_{datetime.now():%Y%m%d_%H%M%S}.png"
        img.save(path)
        return path
    except Exception as exc:
        _log(f"  [vision] screenshot failed: {exc}")
        return None


def _parse_vision_point(text: str) -> dict:
    import json

    text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError(f"no JSON in vision response: {text[:200]}")
        data = json.loads(text[start : end + 1])
    if data.get("found") is False:
        return {"found": False, "label": str(data.get("label") or "")}
    x, y = data.get("x"), data.get("y")
    if x is None or y is None:
        return {"found": False, "label": "missing x/y"}
    return {
        "found": True,
        "x": float(x),
        "y": float(y),
        "label": str(data.get("label") or ""),
        "confidence": str(data.get("confidence") or ""),
    }


def _vision_locate(win, goal: str) -> tuple[float, float] | None:
    if not _prepare_wechat_foreground(win):
        return None
    shot = _screenshot_window(win)
    if not shot:
        return None
    account = _channels_account_hint()
    try:
        from llm_vision_client import vision_chat

        raw = vision_chat(
            system=_VISION_NAV_SYSTEM,
            user_text=(
                f"Goal: {goal}\n"
                f"Creator account name hint: {account}\n"
                "Avoid clicking video thumbnails or the playback center."
            ),
            screenshot=shot,
            max_tokens=160,
        )
        data = _parse_vision_point(raw)
        if not data.get("found"):
            _log(f"  [vision] not found: {goal} ({data.get('label', '')})")
            return None
        x, y = data["x"], data["y"]
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            _log(f"  [vision] out of range ({x},{y})")
            return None
        _log(
            f"  [vision] {goal} -> ({x:.3f},{y:.3f}) "
            f"{data.get('label', '')} conf={data.get('confidence', '?')}"
        )
        return x, y
    except Exception as exc:
        _log(f"  [vision] locate failed: {exc}")
        return None


def _vision_click(win, goal: str) -> bool:
    pt = _vision_locate(win, goal)
    if not pt:
        return False
    _rect_click(win, pt[0], pt[1])
    return True


def _recover_from_feed_video_misclick(
    host, x_ratio: float, y_ratio: float, *, force: bool = False
) -> None:
    if not force and (y_ratio <= 0.12 or y_ratio >= 0.88 or x_ratio < 0.35):
        return
    _log("  [nav] Escape — undo accidental feed video click")
    _force_foreground(int(host.handle))
    time.sleep(0.15)
    send_keys("{ESC}")
    time.sleep(0.8)


def _sidebar_click(win, y_ratio: float) -> None:
    geo = _window_geometry(win)
    if not geo:
        return
    left, top, _, h = geo
    x = left + int(_env("WECHAT_SIDEBAR_X", "35"))
    y = top + int(h * y_ratio)
    _force_foreground(int(win.handle))
    time.sleep(0.15)
    _log(f"  [nav] sidebar click ({x},{y}) y_ratio={y_ratio:.2f}")
    _screen_click(x, y)


def find_channels_host_window():
    """Main WeChat shell (chat + embedded Channels panel)."""
    ranked: list[tuple[int, object]] = []
    for win, title, cls, exe in iter_wechat_windows():
        if "appex" in exe:
            continue
        geo = _window_geometry(win)
        if not geo:
            continue
        _, _, w, h = geo
        if w < 1000 or h < 600:
            continue
        w32 = _win32_window_text(int(win.handle))
        combined = f"{title} {w32}"
        score = w * h
        if title == "微信" or "WeChatMainWnd" in cls:
            score += 500_000
        if any(m in combined for m in ("视频号", "Channels")):
            score += 200_000
        ranked.append((score, win))
    if ranked:
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1]
    return find_main_wechat_window()


def _channels_panel_visible(host) -> bool:
    account = _channels_account_hint()
    markers = ["推荐", "关注", "朋友", "发表视频", "发起直播", "视频号助手", "视频管理"]
    if account:
        markers.append(account)
    _, probe = find_control_deep(*markers)
    if probe:
        return True
    title = _win32_window_text(int(host.handle))
    try:
        title = title or host.window_text() or ""
    except Exception:
        pass
    return any(m in title for m in ("视频号助手", "视频管理", "发表动态"))


def _ensure_channels_panel_open(host) -> None:
    if _channels_panel_visible(host):
        _log("  [nav] channels panel visible")
        return
    _log("  [nav] open Channels from left sidebar...")
    for ratio in _sidebar_channels_y_ratios():
        _sidebar_click(host, ratio)
        time.sleep(1.5)
        if _channels_panel_visible(host):
            _log("  [nav] channels panel opened")
            return
    _log("  [nav] channels panel not confirmed; continue with coordinate nav")


def _detect_publish_session_after_nav(host) -> PublishSession | None:
    found = find_publish_window(timeout=4.0)
    if found:
        sparse = not _window_has_publish_form(found)
        return PublishSession(found, sparse=sparse)

    assistant = find_channels_assistant_window()
    if assistant:
        return PublishSession(assistant, sparse=True)

    _, marker = find_control_deep(
        "上传时长", "点击上传", "添加描述", "发表动态", "视频管理"
    )
    if marker:
        sparse = not _window_has_publish_form(host)
        return PublishSession(host, sparse=sparse)
    return None


def _on_creator_profile_page() -> bool:
    _, live = find_control_deep("发起直播")
    if live:
        return True
    account = _channels_account_hint()
    if account:
        _, acct = find_control_deep(account)
        _, post = find_control_deep("发表视频")
        return bool(acct and post)
    return False


def _wait_publish_session(host, *, timeout: float = 12.0) -> PublishSession | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        session = _detect_publish_session_after_nav(host)
        if session:
            return session
        time.sleep(0.35)

    appex = find_largest_wechat_window(prefer_appex=True)
    if appex:
        geo = _window_geometry(appex)
        if geo and geo[2] >= 600:
            _log("  [nav] using WeChatAppEx publish window (sparse UI)")
            _force_foreground(int(appex.handle))
            return PublishSession(appex, sparse=True)
    return None


def _go_to_creator_profile(host) -> None:
    account = _channels_account_hint()
    profile_goals = [
        f'Click the top tab with creator account name "{account}" (open my profile, NOT "视频号" feed tab)',
        "Click the person/profile icon at the top-right of the Channels panel (open my profile)",
    ]
    if _vision_nav_enabled():
        _log("  [nav] channels home -> My profile (vision LLM)")
        for goal in profile_goals:
            if _vision_click(host, goal):
                time.sleep(1.5)
                if _on_creator_profile_page():
                    return
                _log("  [nav] profile assumed after vision click (sparse UI)")
                return

    _log("  [nav] channels home -> My profile (coordinates)")
    for x_ratio, y_ratio in _channels_my_tab_points():
        _log(f"  [nav] click profile entry ({x_ratio:.2f}, {y_ratio:.2f})")
        _prepare_wechat_foreground(host)
        _rect_click(host, x_ratio, y_ratio)
        time.sleep(1.5)
        if _on_creator_profile_page():
            return
        _recover_from_feed_video_misclick(host, x_ratio, y_ratio)

def _click_open_publish_form(host) -> PublishSession | None:
    post_goal = (
        'Click the "发表视频" (Post video) button on the creator profile page. '
        'It sits beside "发起直播" — NOT a video thumbnail.'
    )

    if _vision_nav_enabled():
        _log("  [nav] profile -> Post video (vision LLM)")
        _prepare_wechat_foreground(host)
        if _vision_click(host, post_goal):
            session = _wait_publish_session(host, timeout=12.0)
            if session:
                _log("  [nav] publish form ready" + (" (sparse UI)" if session.sparse else ""))
                return session

    _, post_btn = find_control_deep("发表视频")
    if post_btn and (_on_creator_profile_page() or _vision_nav_enabled()):
        _log("  [nav] click Post video (UIA)")
        _prepare_wechat_foreground(host)
        _click_control(host, post_btn)
        session = _wait_publish_session(host, timeout=12.0)
        if session:
            _log("  [nav] publish form ready" + (" (sparse UI)" if session.sparse else ""))
            return session

    _log("  [nav] profile -> Post video (coordinates)")
    for x_ratio, y_ratio in _channels_post_video_points():
        _prepare_wechat_foreground(host)
        _log(f"  [nav] click Post video ({x_ratio:.2f}, {y_ratio:.2f})")
        _rect_click(host, x_ratio, y_ratio)
        time.sleep(1.5)
        session = _wait_publish_session(host, timeout=10.0)
        if session:
            _log("  [nav] publish form ready" + (" (sparse UI)" if session.sparse else ""))
            return session
        _recover_from_feed_video_misclick(host, x_ratio, y_ratio)
    return None


def _navigate_channels_home_to_publish(host) -> PublishSession | None:
    _prepare_wechat_foreground(host)

    session = _wait_publish_session(host, timeout=1.5)
    if session:
        _log("  [nav] publish form already open")
        return session

    _ensure_channels_panel_open(host)
    _prepare_wechat_foreground(host)

    if not _on_creator_profile_page():
        _go_to_creator_profile(host)

    if not _on_creator_profile_page():
        _log("  [nav] creator profile not confirmed; trying Post video anyway")

    return _click_open_publish_form(host)


def probe_windows() -> None:
    _log("=== WeChat-related top-level windows ===")
    for win, title, cls, exe in iter_wechat_windows():
        try:
            geo = _window_geometry(win)
            if geo:
                left, top, w, h = geo
                geo_s = f"{w}x{h}@{left},{top}"
            else:
                geo_s = "?"
            w32 = _win32_window_text(int(win.handle))
        except Exception:
            geo_s = "?"
            w32 = ""
        _log(
            f"  uia_title={title!r} win32_title={w32!r} "
            f"class={cls!r} exe={exe!r} size={geo_s}"
        )


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


def find_channels_assistant_window():
    markers = ("视频号助手", "视频管理", "发表动态", "Channels")
    best = None
    best_score = -1
    for win, title, cls, exe in iter_wechat_windows():
        w32 = _win32_window_text(int(win.handle))
        combined = f"{title} {w32}"
        geo = _window_geometry(win)
        if not geo:
            continue
        _, _, w, h = geo
        if w < 480 or h < 360:
            continue
        score = 0
        if any(m in combined for m in markers):
            score += 200
        if "appex" in exe:
            score += 80
        if 650 <= w <= 980:
            score += 40
        if w >= 1100:
            score += 20
        if score > best_score:
            best_score = score
            best = win
    return best if best_score >= 80 else None


def _score_skip_nav_window(win, title: str, exe: str) -> int:
    w32 = _win32_window_text(int(win.handle))
    combined = f"{title} {w32}"
    geo = _window_geometry(win)
    if not geo:
        return -1
    _, _, w, h = geo
    if w < 480 or h < 360:
        return -1
    score = 0
    if any(m in combined for m in ("视频号助手", "视频管理", "发表动态")):
        score += 300
    if "appex" in exe:
        score += 100
    if 650 <= w <= 1000:
        score += 50
    if w >= 1100:
        score += 30
    return score


def find_best_skip_nav_window():
    ranked: list[tuple[int, object, str]] = []
    for win, title, _, exe in iter_wechat_windows():
        score = _score_skip_nav_window(win, title, exe)
        if score >= 0:
            ranked.append((score, win, title or _win32_window_text(int(win.handle))))
    if not ranked:
        return None, ""
    ranked.sort(key=lambda item: item[0], reverse=True)
    score, win, label = ranked[0]
    return win, f"{label!r} score={score}"


def resolve_skip_nav_session() -> PublishSession:
    detected = find_publish_window(timeout=0.8)
    if detected:
        sparse = not _window_has_publish_form(detected)
        _log("  [nav] skip-nav: detected publish form" + (" (sparse UI)" if sparse else ""))
        return PublishSession(detected, sparse=sparse)

    assistant = find_channels_assistant_window()
    if assistant:
        title = _win32_window_text(int(assistant.handle)) or ""
        _log(f"  [nav] skip-nav: channels assistant {title!r} (coordinate mode)")
        _force_foreground(int(assistant.handle))
        return PublishSession(assistant, sparse=True)

    host = find_channels_host_window()
    if host:
        session = _detect_publish_session_after_nav(host)
        if session:
            _log("  [nav] skip-nav: publish form on main window")
            _force_foreground(int(session.window.handle))
            return session

    fg = get_foreground_wechat_window()
    if fg and _window_large_enough(fg):
        title = _win32_window_text(int(fg.handle)) or ""
        try:
            if not title:
                title = fg.window_text() or ""
        except Exception:
            pass
        _log(f"  [nav] skip-nav: foreground {title!r} (coordinate mode)")
        _force_foreground(int(fg.handle))
        return PublishSession(fg, sparse=True)

    best, label = find_best_skip_nav_window()
    if best:
        _log(f"  [nav] skip-nav: best window {label} (coordinate mode)")
        _force_foreground(int(best.handle))
        return PublishSession(best, sparse=True)

    account = _channels_account_hint()
    if account:
        for win, title, _, _ in iter_wechat_windows():
            w32 = _win32_window_text(int(win.handle))
            if account in f"{title} {w32}" and _window_large_enough(win):
                _log(f"  [nav] skip-nav: account window {(title or w32)!r}")
                _force_foreground(int(win.handle))
                return PublishSession(win, sparse=True)

    large = find_largest_wechat_window(prefer_appex=True)
    if large:
        title = _win32_window_text(int(large.handle)) or ""
        _log(f"  [nav] skip-nav: largest WeChatAppEx {title!r}")
        _force_foreground(int(large.handle))
        return PublishSession(large, sparse=True)

    large = find_largest_wechat_window(prefer_appex=False)
    if large:
        _log("  [nav] skip-nav: largest WeChat window")
        _force_foreground(int(large.handle))
        return PublishSession(large, sparse=True)

    raise PcWeChatPublishError(
        "skip-nav: no WeChat window found. Run without --skip-nav from channels home, "
        "or run --probe to list windows."
    )


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
    for ratio in _sidebar_channels_y_ratios():
        _sidebar_click(main_win, ratio)
        time.sleep(1.5)
        _, probe = find_control_deep("发表视频", "发起直播", _channels_account_hint())
        if probe:
            return True
        if _channels_panel_visible(main_win):
            return True
    return False


def open_publish_page(*, skip_nav: bool = False) -> PublishSession:
    if skip_nav:
        return resolve_skip_nav_session()

    existing = find_publish_window(timeout=1.0)
    if existing:
        _log("  [nav] publish form already open")
        return PublishSession(existing, sparse=not _window_has_publish_form(existing))

    host = find_channels_host_window()
    if host:
        _log("  [nav] from channels home (default daily page)")
        session = _navigate_channels_home_to_publish(host)
        if session:
            return session

    shot = _save_debug_shot("nav_fail")
    hint = f" screenshot: {shot}" if shot else ""
    raise PcWeChatPublishError(
        "cannot open publish form from channels home. Minimize PowerShell, keep WeChat "
        f"Channels feed (视频号首页) visible, then rerun.{hint} "
        "Set WECHAT_VISION_NAV=1 + AIHUBMIX_API_KEY, or tune "
        "WECHAT_CHANNELS_MY_* / WECHAT_POST_VIDEO_* in .env."
    )


def _enum_file_dialog_hwnds(*, folder_hint: str = "") -> list[tuple[int, str, str]]:
    import win32gui

    found: list[tuple[int, str, str]] = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        cls = win32gui.GetClassName(hwnd) or ""
        title = (win32gui.GetWindowText(hwnd) or "").strip()
        if cls == "#32770":
            found.append((hwnd, cls, title))
            return True
        if cls == "CabinetWClass":
            lower = title.lower()
            if folder_hint and folder_hint in title:
                found.append((hwnd, cls, title))
            elif any(k in title for k in ("打开", "Open", "选择", "上传")):
                found.append((hwnd, cls, title))
        return True

    win32gui.EnumWindows(_cb, None)
    return found


def _pick_file_dialog_hwnd(*, folder_hint: str = "") -> int | None:
    dialogs = _enum_file_dialog_hwnds(folder_hint=folder_hint)
    if not dialogs:
        return None
    for hwnd, cls, title in dialogs:
        if cls == "#32770" and any(k in title for k in ("打开", "Open", "选择", "上传", "文件")):
            return hwnd
    for hwnd, cls, _ in dialogs:
        if cls == "#32770":
            return hwnd
    return dialogs[0][0]


def _file_dialog_window(*, folder_hint: str = ""):
    hwnd = _pick_file_dialog_hwnd(folder_hint=folder_hint)
    if hwnd:
        try:
            return _desktop_uia().window(handle=hwnd)
        except Exception:
            try:
                from pywinauto import Application

                return Application(backend="win32").connect(handle=hwnd, timeout=2).window(
                    handle=hwnd
                )
            except Exception:
                pass

    desktop = _desktop_uia()
    for win in desktop.windows():
        try:
            cls = win.class_name() or ""
            title = (win.window_text() or "").strip()
            if cls == "#32770":
                return win
            lower = title.lower()
            if any(k in title for k in ("打开", "Open", "选择文件", "选择要上传的文件")):
                return win
            if "file" in lower and "open" in lower:
                return win
        except Exception:
            continue
    return None


def _file_dialog_visible(*, folder_hint: str = "") -> bool:
    return _pick_file_dialog_hwnd(folder_hint=folder_hint) is not None


def _dialog_still_open(*, folder_hint: str = "") -> bool:
    return _file_dialog_visible(folder_hint=folder_hint)


def _submit_path_via_win32(hwnd: int, path_str: str, *, cls: str = "#32770") -> None:
    import win32con
    import win32gui

    _force_foreground(hwnd)
    time.sleep(0.35)
    _log(f"  [upload] focus dialog hwnd={hwnd} class={cls!r}")

    if cls == "#32770":
        try:
            edit = win32gui.GetDlgItem(hwnd, 1148)
            if edit:
                win32gui.SendMessage(edit, win32con.WM_SETTEXT, 0, path_str)
                time.sleep(0.15)
                open_btn = win32gui.GetDlgItem(hwnd, 1)
                if open_btn:
                    win32gui.PostMessage(open_btn, win32con.BM_CLICK, 0, 0)
                    return
        except Exception:
            pass

    _set_clipboard_text(path_str)
    if cls == "CabinetWClass":
        send_keys("%d")
        time.sleep(0.2)
        send_keys("^a^v{ENTER}")
        time.sleep(0.4)
        send_keys("^a^v{ENTER}")
        return

    for combo in ("%n", "%l"):
        send_keys(combo)
        time.sleep(0.15)
    send_keys("^a^v")
    time.sleep(0.15)
    send_keys("{ENTER}")


def _submit_path_to_open_dialog(path_str: str, video: Path | None = None) -> bool:
    folder_hint = video.parent.name if video else ""
    dialogs = _enum_file_dialog_hwnds(folder_hint=folder_hint)
    if not dialogs:
        return False

    for hwnd, cls, title in dialogs:
        _log(f"  [upload] try dialog {cls!r} title={title!r}")
        _submit_path_via_win32(hwnd, path_str, cls=cls)
        time.sleep(0.8)
        if not _dialog_still_open(folder_hint=folder_hint):
            return True

    dlg = _file_dialog_window(folder_hint=folder_hint)
    if dlg is not None:
        _submit_path_to_file_dialog(dlg, path_str)
        time.sleep(0.8)
        if not _dialog_still_open(folder_hint=folder_hint):
            return True
    return False


def _try_blind_file_path(path_str: str, *, hwnd: int | None = None) -> None:
    if hwnd:
        _submit_path_via_win32(hwnd, path_str)
        return
    _set_clipboard_text(path_str)
    send_keys("^a^v")
    time.sleep(0.15)
    send_keys("{ENTER}")


def _upload_points_for_window(win) -> list[tuple[float, float]]:
    custom_x = _env("WECHAT_UPLOAD_X_RATIO", "")
    custom_y = _env("WECHAT_UPLOAD_Y_RATIO", "")
    if custom_x and custom_y:
        return [(float(custom_x), float(custom_y))]

    geo = _window_geometry(win)
    if geo and geo[2] >= 1050:
        return [
            (0.46, 0.43),
            (0.48, 0.46),
            (0.44, 0.40),
            (0.50, 0.44),
            (0.42, 0.45),
            (0.52, 0.42),
            (0.46, 0.48),
        ]
    return [
        (0.35, 0.45),
        (0.38, 0.42),
        (0.40, 0.48),
        (0.32, 0.44),
        (0.42, 0.40),
    ]


def _upload_coordinate_grid(win, *, path_str: str = "") -> None:
    abs_x = _env("WECHAT_UPLOAD_ABS_X", "")
    abs_y = _env("WECHAT_UPLOAD_ABS_Y", "")
    if abs_x and abs_y:
        ax, ay = int(abs_x), int(abs_y)
        _log(f"  [upload] absolute screen click ({ax}, {ay})")
        _screen_click(ax, ay)
        time.sleep(0.8)
        if _file_dialog_visible():
            _log("  [upload] file dialog opened")
            return
        if path_str:
            _try_blind_file_path(path_str)
            time.sleep(0.8)
            if not _file_dialog_visible():
                return
        raise PcWeChatPublishError("upload click missed; adjust WECHAT_UPLOAD_ABS_X/Y")

    points = _upload_points_for_window(win)
    for x_ratio, y_ratio in points:
        _log(f"  [upload] click upload zone ({x_ratio:.2f}, {y_ratio:.2f})")
        _rect_click(win, x_ratio, y_ratio)
        time.sleep(0.5)
        if _file_dialog_visible():
            hwnd = _pick_file_dialog_hwnd()
            title = _win32_window_text(hwnd) if hwnd else ""
            _log(f"  [upload] file dialog opened hwnd={hwnd} title={title!r}")
            return
        _rect_click(win, x_ratio, y_ratio, double=True)
        time.sleep(0.5)
        if _file_dialog_visible():
            hwnd = _pick_file_dialog_hwnd()
            title = _win32_window_text(hwnd) if hwnd else ""
            _log(f"  [upload] file dialog opened (double-click) hwnd={hwnd} title={title!r}")
            return
        if path_str:
            _try_blind_file_path(path_str)
            time.sleep(0.6)
            if not _file_dialog_visible():
                _log("  [upload] blind path entry accepted")
                return

    raise PcWeChatPublishError(
        "upload zone click missed (no file dialog). "
        "Run --probe, click publish page before script, or set "
        "WECHAT_UPLOAD_X_RATIO/Y_RATIO or WECHAT_UPLOAD_ABS_X/Y in .env"
    )


def _submit_path_to_file_dialog(dlg, path_str: str) -> None:
    try:
        dlg.set_focus()
    except Exception:
        pass
    edits = []
    try:
        edits = [e for e in dlg.descendants(control_type="Edit") if e.is_visible()]
    except Exception:
        pass
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
                return
        except Exception:
            pass
    send_keys("{ENTER}")


def _pick_file_in_dialog(video: Path, *, timeout: float = 45.0) -> None:
    path_str = str(video.resolve())
    folder_hint = video.parent.name
    deadline = time.time() + timeout

    while time.time() < deadline:
        if not _dialog_still_open(folder_hint=folder_hint):
            _log(f"  [upload] selected: {video.name}")
            return
        if _submit_path_to_open_dialog(path_str, video):
            _log(f"  [upload] selected: {video.name}")
            return
        time.sleep(0.35)

    if _submit_path_to_open_dialog(path_str, video):
        _log(f"  [upload] selected: {video.name}")
        return
    if not _dialog_still_open(folder_hint=folder_hint):
        _log(f"  [upload] selected: {video.name}")
        return

    dialogs = _enum_file_dialog_hwnds(folder_hint=folder_hint)
    dlg_tail = "; ".join(f"{cls}|{title}" for _, cls, title in dialogs[:4])
    seen = []
    for win in _desktop_uia().windows():
        try:
            seen.append(f"{win.class_name()}|{win.window_text()}")
        except Exception:
            pass
    tail = "; ".join(seen[:8])
    extra = f" win32_dialogs={dlg_tail or 'none'};" if dlg_tail else " "
    raise PcWeChatPublishError(f"file open dialog timeout.{extra} visible={tail}")


def _click_upload_area(win, *, sparse: bool = False, path_str: str = "") -> None:
    if not sparse:
        host, label = find_control_deep("上传时长", "点击上传", "MP4", "H.264")
        if label:
            _click_control(host, label)
            time.sleep(0.5)
            if _file_dialog_visible():
                return

        hints = ("上传时长", "点击上传", "上传视频", "MP4")
        for hint in hints:
            try:
                label = win.child_window(title_re=f".*{hint}.*")
                if label.exists(timeout=0.5):
                    label.click_input()
                    time.sleep(0.5)
                    if _file_dialog_visible():
                        return
            except Exception:
                pass

    _upload_coordinate_grid(win, path_str=path_str)


def upload_video(session: PublishSession, video: Path) -> None:
    win = session.window
    if not video.is_file():
        raise PcWeChatPublishError(f"video not found: {video}")
    path_str = str(video.resolve())
    folder_hint = video.parent.name
    _log(f"  [upload] {video}")
    _click_upload_area(win, sparse=session.sparse, path_str=path_str)
    if _file_dialog_visible(folder_hint=folder_hint):
        _log("  [upload] submitting path to open dialog...")
        if _submit_path_to_open_dialog(path_str, video):
            _log(f"  [upload] selected: {video.name}")
            return
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


def wait_upload_done(session: PublishSession, *, timeout: float = 600.0) -> None:
    win = session.window
    _log("  [upload] waiting for processing...")
    deadline = time.time() + timeout
    last_log = 0.0
    while time.time() < deadline:
        if not session.sparse and _is_uploading(win):
            if time.time() - last_log > 8:
                _log("  [upload] still processing...")
                last_log = time.time()
            time.sleep(2.0)
            continue
        if not session.sparse and _publish_button(win, click=False):
            _log("  [upload] ready")
            return
        if session.sparse:
            time.sleep(8.0)
            _log("  [upload] sparse UI: assume upload slot ready (fixed wait)")
            return
        time.sleep(2.0)
    raise PcWeChatPublishError("upload/processing timeout")


def fill_description(session: PublishSession, body: str) -> None:
    if not body.strip():
        raise PcWeChatPublishError("empty description")

    win = session.window
    _log(f"  [form] description ({len(body)} chars)")

    if not session.sparse:
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

    _log("  [form] coordinate + clipboard paste")
    _rect_click(
        win,
        _ratio_env("WECHAT_DESC_X_RATIO", 0.68),
        _ratio_env("WECHAT_DESC_Y_RATIO", 0.30),
    )
    time.sleep(0.3)
    _set_clipboard_text(body)
    send_keys("^a")
    send_keys("^v")


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


def click_publish(session: PublishSession) -> None:
    win = session.window
    _log("  [publish] click publish...")
    if not session.sparse and _publish_button(win, click=True):
        return
    _log("  [publish] coordinate click publish button")
    _rect_click(
        win,
        _ratio_env("WECHAT_PUBLISH_X_RATIO", 0.88),
        _ratio_env("WECHAT_PUBLISH_Y_RATIO", 0.93),
    )


def publish_via_pc_wechat(
    video: Path,
    body: str,
    *,
    skip_nav: bool = False,
    no_publish: bool = False,
) -> dict:
    session = open_publish_page(skip_nav=skip_nav)
    upload_video(session, video)
    wait_upload_done(session)
    fill_description(session, body)
    if no_publish:
        _log("  [publish] --no-publish: uploaded and filled, not submitted")
        return {"published": False, "video": str(video), "sparse_ui": session.sparse}

    click_publish(session)
    time.sleep(2.0)
    _log("  [publish] clicked publish (confirm in WeChat UI)")
    return {
        "published": True,
        "video": str(video),
        "desc_len": len(body),
        "sparse_ui": session.sparse,
    }


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
