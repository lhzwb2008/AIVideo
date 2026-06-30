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


def _resolve_script_path(
    pack_dir: Path | None, video: Path, *, explicit: Path | None = None
) -> Path | None:
    if explicit and explicit.is_file():
        return explicit
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
    script_override: Path | None = None,
) -> dict[str, str]:
    from douyin_caption import _strip_urls
    from publish_resolve import load_script
    from social_caption import build_social_fields

    script_path = _resolve_script_path(pack_dir, video, explicit=script_override)
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


def _scroll_window(win, x_ratio: float, y_ratio: float, wheel_dist: int) -> None:
    """Mouse-wheel scroll inside a window at the given ratio point.

    wheel_dist < 0 scrolls DOWN (toward the bottom of the form).
    """
    geo = _window_geometry(win)
    if not geo:
        return
    left, top, w, h = geo
    x = left + int(w * x_ratio)
    y = top + int(h * y_ratio)
    try:
        _focus_hwnd(int(win.handle))
        time.sleep(0.1)
        mouse.scroll(coords=(x, y), wheel_dist=wheel_dist)
    except Exception:
        pass


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
        if attempt in (0, 2):
            _click_taskbar_wechat()
        _force_foreground(hwnd)
        time.sleep(0.35 + attempt * 0.12)
        if _is_wechat_foreground(win):
            return True
    try:
        import win32gui

        if not win32gui.IsIconic(hwnd) and win32gui.IsWindowVisible(hwnd):
            _log("  [nav] WeChat window visible; vision will crop window area")
            return True
    except Exception:
        pass
    _log("  [nav] warning: WeChat not foreground — will still try window screenshot")
    return False


def _click_taskbar_wechat() -> bool:
    labels = ("微信", "WeChat", "视频号")
    try:
        taskbar = _desktop_uia().window(class_name="Shell_TrayWnd")
        for btn in taskbar.descendants(control_type="Button"):
            name = (btn.window_text() or "").strip()
            if not name:
                continue
            if any(label in name for label in labels):
                _log(f"  [nav] taskbar click {name!r}")
                btn.click_input()
                time.sleep(0.7)
                return True
    except Exception as exc:
        _log(f"  [nav] taskbar click: {exc}")
    for win, title, _, exe in iter_wechat_windows():
        if "wechat" not in exe and title not in labels:
            continue
        try:
            import win32con
            import win32gui

            hwnd = int(win.handle)
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            _force_foreground(hwnd)
            time.sleep(0.4)
            return True
        except Exception:
            continue
    return False


def _activate_wechat_app(win) -> None:
    _log("  [nav] activate WeChat (taskbar + restore + focus)")
    _click_taskbar_wechat()
    try:
        import win32con
        import win32gui

        hwnd = int(win.handle)
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    except Exception:
        pass
    _force_foreground(int(win.handle))
    time.sleep(0.45)


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
    return _env("WECHAT_CHANNELS_ACCOUNT") or _env("AIVIDEO_BRAND_NAME", "AI财知道2026")


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
    # On the standalone 视频号 account window (browser-like, ~650px wide), the
    # "📷 发表视频" button sits on the LEFT, below the avatar/follower count and
    # ABOVE the video grid — roughly ratio (0.13, 0.29). "发起直播" is at ~0.25.
    # (Ratios are relative to that account window, not the 1840px main shell.)
    return [
        (0.13, 0.29),
        (0.16, 0.29),
        (0.13, 0.31),
        (0.18, 0.28),
        (0.11, 0.30),
        (0.20, 0.29),
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
    _prepare_wechat_foreground(win)
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
    """Best window for Channels UI: standalone 视频号 (WeChatAppEx) or main WeChat shell."""
    account = _channels_account_hint()
    ranked: list[tuple[int, object]] = []
    for win, title, cls, exe in iter_wechat_windows():
        geo = _window_geometry(win)
        if not geo:
            continue
        _, _, w, h = geo
        if w < 520 or h < 400:
            continue
        w32 = _win32_window_text(int(win.handle))
        combined = f"{title} {w32}"
        score = w * h
        if "appex" in exe:
            score += 400_000
        if title == "视频号" or "视频号" in combined:
            score += 700_000
        if account and account in combined:
            score += 500_000
        if title == "微信" or "WeChatMainWnd" in cls:
            score += 250_000
        if w >= 1000:
            score += 80_000
        ranked.append((score, win))
    if ranked:
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1]
    return find_main_wechat_window() or find_largest_wechat_window(prefer_appex=True)


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
    found, sparse = find_best_publish_session_window(timeout=1.0)
    if found and _is_real_publish_form(found):
        return PublishSession(found, sparse=sparse)

    assistant = find_channels_assistant_window()
    if assistant and _is_real_publish_form(assistant):
        sparse = not _control_exists_on_window(
            assistant, "上传时长", "封面预览", "发表动态"
        )
        return PublishSession(assistant, sparse=sparse)

    if host and _is_real_publish_form(host):
        sparse = not _control_exists_on_window(host, "上传时长", "封面预览", "发表动态")
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
        if geo and geo[2] >= 600 and _is_real_publish_form(appex):
            _log("  [nav] using WeChatAppEx publish window (sparse UI)")
            _force_foreground(int(appex.handle))
            return PublishSession(appex, sparse=True)
    return None


def _vision_profile_page_visible(win) -> bool:
    if not _vision_nav_enabled():
        return False
    shot = _screenshot_window(win)
    if not shot:
        return False
    try:
        from llm_vision_client import vision_chat

        raw = vision_chat(
            system='Return ONLY JSON: {"profile_page":true/false,"has_post_video":true/false}',
            user_text=(
                "Is this the WeChat Channels creator profile page showing "
                '"发表视频" and "发起直播" buttons below the avatar (NOT the vertical video feed)?'
            ),
            screenshot=shot,
            max_tokens=80,
        )
        import json

        text = raw.strip()
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start : end + 1]) if start >= 0 else {}
        ok = bool(data.get("profile_page") and data.get("has_post_video"))
        if ok:
            _log("  [vision] creator profile page detected")
        return ok
    except Exception as exc:
        _log(f"  [vision] profile detect: {exc}")
        return False


def _go_to_creator_profile(host) -> None:
    account = _channels_account_hint()
    profile_goals = [
        f'Click the top tab whose title contains "{account}" (creator profile, NOT the "视频号" feed tab)',
        "Click the person/profile icon at the top-right of the Channels panel (open my profile)",
    ]
    if _vision_nav_enabled():
        _log("  [nav] channels home -> My profile (vision LLM)")
        for goal in profile_goals:
            if _vision_click(host, goal):
                time.sleep(1.5)
                if _on_creator_profile_page() or _vision_profile_page_visible(host):
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

def _pick_profile_like_window(host):
    """Return (win, page) for the window currently showing the publish entry
    (创作者主页 profile or 视频号助手 management list), preferring management_list."""
    if not _vision_nav_enabled():
        return host, ""
    best = None
    for w, _, _, _ in iter_wechat_windows():
        geo = _window_geometry(w)
        if not geo or geo[2] < 450:
            continue
        page = _vision_classify_page(w)
        if page == "publish_form":
            return w, page
        if page == "management_list":
            return w, page
        if page == "profile" and best is None:
            best = (w, page)
    return best if best else (host, "")


def _click_open_publish_form(host) -> PublishSession | None:
    post_goal = (
        'Click the "发表视频" button (a small pill button with a camera icon, '
        'next to "发起直播"). On the creator profile it is on the LEFT side, '
        "BELOW the avatar and the follower count (xx人关注), and ABOVE the grid "
        "of video thumbnails. On the 视频号助手 page it is at the top-right. "
        "NEVER click a video thumbnail/cover in the grid."
    )

    if _vision_nav_enabled():
        for attempt in range(4):
            # If a publish form is already open anywhere (e.g. opened by a prior
            # attempt), reuse it instead of clicking 发表视频 again — this stops
            # the "several 发表动态 windows opened and jumped around" problem.
            existing = _wait_publish_session(host, timeout=1.0)
            if existing:
                _log("  [nav] publish form ready (already open)")
                return existing

            target, page = _pick_profile_like_window(host)
            if page == "publish_form":
                session = _wait_publish_session(target, timeout=6.0)
                if session:
                    _log("  [nav] publish form ready")
                    return session
            if page not in ("profile", "management_list"):
                _log(f"  [nav] not on profile/management (page={page or '?'}); re-navigate")
                _go_to_creator_profile(host)
                target, page = _pick_profile_like_window(host)
                if page not in ("profile", "management_list", "publish_form"):
                    continue
            _log(f"  [nav] -> Post video (vision, attempt {attempt + 1}, page={page})")
            _prepare_wechat_foreground(target)
            if not _vision_click(target, post_goal):
                continue
            time.sleep(1.5)
            # The publish form usually opens in a SEPARATE window, so scan ALL
            # windows for it rather than only re-checking the clicked window.
            session = _wait_publish_session(host, timeout=8.0)
            if session:
                _log("  [nav] publish form ready" + (" (sparse UI)" if session.sparse else ""))
                return session
            after = _vision_classify_page(target, force=True)
            if after == "feed":
                _log("  [nav] landed on feed video — Escape and retry")
                try:
                    _force_foreground(int(target.handle))
                    time.sleep(0.15)
                    send_keys("{ESC}")
                    time.sleep(0.8)
                except Exception:
                    pass
        # vision path exhausted; fall through to UIA/coordinate attempts.

    owner, post_btn = find_control_deep("发表视频")
    target = owner or host
    if post_btn:
        _log("  [nav] click Post video (UIA on owning window)")
        _prepare_wechat_foreground(target)
        _click_control(target, post_btn)
        time.sleep(1.0)
        session = _wait_publish_session(target, timeout=10.0)
        if session:
            _log("  [nav] publish form ready" + (" (sparse UI)" if session.sparse else ""))
            return session

    _log("  [nav] -> Post video (coordinates)")
    for x_ratio, y_ratio in _channels_post_video_points():
        _prepare_wechat_foreground(target)
        _log(f"  [nav] click Post video ({x_ratio:.2f}, {y_ratio:.2f})")
        _rect_click(target, x_ratio, y_ratio)
        time.sleep(1.5)
        session = _wait_publish_session(target, timeout=8.0)
        if session:
            _log("  [nav] publish form ready" + (" (sparse UI)" if session.sparse else ""))
            return session
        _recover_from_feed_video_misclick(target, x_ratio, y_ratio)
    return None


def _navigate_channels_home_to_publish(host) -> PublishSession | None:
    _activate_wechat_app(host)
    _prepare_wechat_foreground(host)

    session = _wait_publish_session(host, timeout=1.5)
    if session:
        _log("  [nav] publish form already open")
        return session

    # Shortcut for continuous publishing: after a successful post we land on the
    # 视频管理 list page where "发表视频" is directly reachable; click it without
    # detouring back through the profile tab.
    _, post_btn = find_control_deep("发表视频")
    if post_btn:
        _log("  [nav] '发表视频' directly available — open publish form")
        session = _click_open_publish_form(host)
        if session:
            return session

    if _on_creator_profile_page() or _vision_profile_page_visible(host):
        _log("  [nav] creator profile ready — click Post video")
        return _click_open_publish_form(host)

    _ensure_channels_panel_open(host)
    _activate_wechat_app(host)

    if not _on_creator_profile_page():
        _go_to_creator_profile(host)

    if not _on_creator_profile_page() and not _vision_profile_page_visible(host):
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


def _is_video_feed_page(win) -> bool:
    """Vertical Channels feed / playback — not the publish form."""
    if _control_exists_on_window(win, "赞和收藏", "朋友赞过", "关注"):
        if not _control_exists_on_window(
            win, "封面预览", "上传时长", "发表动态", "短标题", "视频管理"
        ):
            return True
    if _vision_nav_enabled():
        shot = _screenshot_window(win)
        if shot:
            try:
                from llm_vision_client import vision_chat
                import json

                raw = vision_chat(
                    system='Return ONLY JSON: {"video_feed":true/false}',
                    user_text=(
                        "Is this the WeChat Channels vertical VIDEO FEED with playback "
                        "(progress bar, like/share icons on the side)? NOT the publish/upload form."
                    ),
                    screenshot=shot,
                    max_tokens=60,
                )
                start, end = raw.find("{"), raw.rfind("}")
                data = json.loads(raw[start : end + 1]) if start >= 0 else {}
                if data.get("video_feed"):
                    _log("  [nav] vision: Channels video feed (not publish form)")
                    return True
            except Exception:
                pass
    return False


def _control_exists_on_window(win, *needles: str) -> bool:
    for needle in needles:
        if not needle:
            continue
        try:
            if win.child_window(title=needle).exists(timeout=0.12):
                return True
            if win.child_window(title_re=f".*{needle}.*").exists(timeout=0.12):
                return True
        except Exception:
            pass
        try:
            for elem in win.descendants():
                try:
                    if not elem.is_visible():
                        continue
                    name = (elem.window_text() or "").strip()
                    if needle in name:
                        return True
                except Exception:
                    continue
        except Exception:
            pass
    return False


def _window_has_publish_form(win) -> bool:
    return _is_real_publish_form(win)


_PUBLISH_FORM_ONLY = (
    "封面预览",
    "上传时长",
    "视频描述",
    "添加描述",
    "发表动态",
    "点击上传",
    "添加到合集",
    "声明原创",
    "定时发表",
)


_VISION_PAGE_CACHE: dict[int, tuple[float, str]] = {}
_VISION_PAGE_TTL = 2.0


def _vision_classify_page(win, *, force: bool = False) -> str:
    """Ask the vision LLM which WeChat Channels page this is.

    Returns one of: publish_form, management_list, feed, profile, other, "".
    Empty string means vision unavailable / failed.

    Results are cached per window handle for a short TTL so the navigation /
    detection polling loops don't fire one LLM call every 0.35s (which made the
    run look frozen and was very slow/expensive). Pass force=True right after a
    click to bypass the cache and re-read the (now changed) page.
    """
    if not _vision_nav_enabled():
        return ""
    try:
        handle = int(win.handle)
    except Exception:
        handle = 0
    now = time.time()
    if handle and not force:
        cached = _VISION_PAGE_CACHE.get(handle)
        if cached and (now - cached[0]) < _VISION_PAGE_TTL:
            return cached[1]
    shot = _screenshot_window(win)
    if not shot:
        return ""
    try:
        from llm_vision_client import vision_chat
        import json

        raw = vision_chat(
            system='Return ONLY JSON: {"page":"publish_form|management_list|feed|profile|other"}',
            user_text=(
                "Classify this WeChat Channels (视频号) screen:\n"
                "- publish_form: the POST/PUBLISH editor with an upload slot OR a single "
                "video preview plus 视频描述/封面预览/短标题 fields and an orange 发表 button.\n"
                "- management_list: 视频管理 page showing a GRID/LIST of already-published "
                "videos with view/like counts (浏览/点赞), NOT an editor.\n"
                "- feed: vertical video player feed.\n"
                "- profile: creator profile with 发表视频/发起直播 buttons under the avatar.\n"
                "- other: anything else."
            ),
            screenshot=shot,
            max_tokens=40,
        )
        start, end = raw.find("{"), raw.rfind("}")
        data = json.loads(raw[start : end + 1]) if start >= 0 else {}
        page = str(data.get("page", "")).strip()
        if page:
            _log(f"  [vision] page = {page}")
            if handle:
                _VISION_PAGE_CACHE[handle] = (time.time(), page)
        return page
    except Exception as exc:
        _log(f"  [vision] classify: {exc}")
        return ""


def _is_management_list_page(win) -> bool:
    """The creator profile / 视频管理 list page where you START a post.

    It exposes a "发表视频" entry button and a grid of already-published videos,
    but it is NOT the publish form. Detecting it as a form makes the navigator
    skip opening a fresh form, then fail to find the publish button.
    """
    # A real form field present => definitely not the management list.
    if _control_exists_on_window(win, *_PUBLISH_FORM_ONLY):
        return False
    has_entry = _control_exists_on_window(win, "发表视频", "发起直播")
    if not has_entry:
        _, post_btn = find_control_deep("发表视频")
        _, live_btn = find_control_deep("发起直播")
        has_entry = bool(post_btn or live_btn)
    if has_entry:
        return True
    # Sparse UIA (WeChatAppEx) hides controls — let vision decide.
    page = _vision_classify_page(win)
    return page in ("management_list", "profile", "feed")


def _is_real_publish_form(win) -> bool:
    if _is_video_feed_page(win):
        return False
    title = ""
    try:
        title = win.window_text() or ""
    except Exception:
        pass
    w32 = _win32_window_text(int(win.handle))
    combined = f"{title} {w32}"
    strong = (
        "上传时长",
        "点击上传",
        "封面预览",
        "发表动态",
        "视频描述",
        "添加描述",
        "短标题",
        "填写短标题",
        "添加到合集",
        "定时发表",
        "声明原创",
    )
    # Vision is AUTHORITATIVE and runs BEFORE UIA strong markers: the 视频管理
    # list page (视频号助手) shares the WeChatAppEx renderer and can expose stale
    # controls like 短标题/删除, which falsely match strong markers. Trust vision
    # to reject management/profile/feed pages so the navigator opens a fresh form.
    if _vision_nav_enabled():
        page = _vision_classify_page(win)
        if page == "publish_form":
            _log("  [nav] vision: publish form detected")
            return True
        if page in ("management_list", "profile", "feed"):
            return False
        # page == "other" / "" => fall through to UIA heuristics below.
    if "发表动态" in combined or _control_exists_on_window(win, *strong):
        return True
    if _is_management_list_page(win):
        return False
    if _control_exists_on_window(win, "删除") and _control_exists_on_window(
        win, "视频描述"
    ):
        return True
    return False


def find_publish_window(*, timeout: float = 20.0):
    win, _ = find_best_publish_session_window(timeout=timeout)
    return win


def find_best_publish_session_window(
    *, timeout: float = 20.0
) -> tuple[object | None, bool]:
    """Return (window, sparse). Prefer standalone 视频号助手 over main 微信 shell."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        assistant = find_channels_assistant_window()
        if assistant and _is_real_publish_form(assistant):
            sparse = not _control_exists_on_window(
                assistant, "上传时长", "封面预览", "发表动态"
            )
            try:
                assistant.set_focus()
            except Exception:
                pass
            return assistant, sparse

        ranked: list[tuple[int, object]] = []
        for win, title, cls, exe in iter_wechat_windows():
            if not _is_real_publish_form(win):
                continue
            geo = _window_geometry(win)
            if not geo:
                continue
            _, _, w, h = geo
            w32 = _win32_window_text(int(win.handle))
            combined = f"{title} {w32}"
            score = 0
            if "appex" in exe:
                score += 500
            if any(m in combined for m in ("视频号助手", "发表动态", "视频管理")):
                score += 400
            if 600 <= w <= 1000:
                score += 200
            if title == "微信" and w >= 1100:
                score += 50
            ranked.append((score, win))

        if ranked:
            ranked.sort(key=lambda item: item[0], reverse=True)
            win = ranked[0][1]
            sparse = not _window_has_publish_form(win)
            try:
                win.set_focus()
            except Exception:
                pass
            return win, sparse
        time.sleep(0.35)
    return None, True


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
            score += 120
        if "Chrome_WidgetWin" in cls or "Qt515" in cls:
            score += 80
        if 650 <= w <= 980:
            score += 60
        if _window_has_publish_form(win):
            score += 300
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

    existing, sparse = find_best_publish_session_window(timeout=1.5)
    if existing and _is_real_publish_form(existing):
        title = _win32_window_text(int(existing.handle)) or ""
        _log(f"  [nav] publish form already open ({title!r})")
        _activate_wechat_app(existing)
        return PublishSession(existing, sparse=sparse)

    host = find_channels_host_window()
    if host:
        title = _win32_window_text(int(host.handle)) or ""
        if _is_video_feed_page(host):
            _log(f"  [nav] on Channels feed ({title!r}) — navigate to Post video")
        elif _is_real_publish_form(host):
            _log(f"  [nav] publish form on {title!r}")
            return PublishSession(host, sparse=sparse)
        else:
            _log(f"  [nav] target window {title!r}")
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


def _is_explorer_search_window(title: str) -> bool:
    bad = ("搜索结果", "中的搜索", "Search Results")
    return any(b in title for b in bad)


def _is_valid_cabinet_file_picker(title: str) -> bool:
    if _is_explorer_search_window(title):
        return False
    if any(k in title for k in ("打开", "Open", "选择文件", "选择要上传", "Browse")):
        return True
    if title.endswith("文件资源管理器") or title == "文件资源管理器":
        return False
    return False


def _dismiss_stray_upload_windows() -> None:
    import win32con
    import win32gui

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = (win32gui.GetWindowText(hwnd) or "").strip()
        cls = win32gui.GetClassName(hwnd) or ""
        close = False
        if "WMP Skin Host" in title or "正在播放" in title:
            close = True
        if cls == "CabinetWClass" and _is_explorer_search_window(title):
            close = True
        if close:
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception:
                pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass


def _video_slot_filled(session: PublishSession) -> bool:
    win = session.window
    # On the 视频管理 list page each published video also has a 删除 button, so
    # never trust 删除 unless we are actually on the publish form.
    if _is_management_list_page(win):
        _log("  [upload] management list page — not a publish form, will navigate")
        return False
    if _vision_nav_enabled():
        page = _vision_classify_page(win)
        if page and page != "publish_form":
            _log(f"  [upload] vision page={page} — not a publish form, will navigate")
            return False
    _, delete_btn = find_control_deep("删除")
    if delete_btn:
        _log("  [upload] detected 删除 — video already on form")
        return True
    _, cover = find_control_deep("封面预览")
    if cover:
        _log("  [upload] detected 封面预览 — video already on form")
        return True
    try:
        if win.child_window(title_re=".*删除.*").exists(timeout=0.2):
            _log("  [upload] detected 删除 — video already on form")
            return True
        if win.child_window(title_re=".*封面预览.*").exists(timeout=0.2):
            _log("  [upload] detected 封面预览 — video already on form")
            return True
    except Exception:
        pass
    if _vision_nav_enabled():
        shot = _screenshot_window(win)
        if shot:
            try:
                from llm_vision_client import vision_chat
                import json

                raw = vision_chat(
                    system='Return ONLY JSON: {"video_uploaded":true/false}',
                    user_text=(
                        "On this WeChat Channels publish form, is a video already uploaded "
                        "(preview/thumbnail with 删除 or cover visible), NOT an empty upload slot?"
                    ),
                    screenshot=shot,
                    max_tokens=60,
                )
                start, end = raw.find("{"), raw.rfind("}")
                data = json.loads(raw[start : end + 1]) if start >= 0 else {}
                if data.get("video_uploaded"):
                    _log("  [upload] vision: video slot already filled")
                    return True
            except Exception:
                pass
    return False


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
        if cls == "CabinetWClass" and _is_valid_cabinet_file_picker(title):
            found.append((hwnd, cls, title))
        return True

    win32gui.EnumWindows(_cb, None)
    return found


def _pick_file_dialog_hwnd(*, folder_hint: str = "") -> int | None:
    dialogs = _enum_file_dialog_hwnds(folder_hint=folder_hint)
    if not dialogs:
        return None
    for hwnd, cls, title in dialogs:
        if cls == "#32770":
            return hwnd
    for hwnd, cls, title in dialogs:
        if cls == "CabinetWClass" and _is_valid_cabinet_file_picker(title):
            return hwnd
    return None


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


def _native_file_dialog_visible() -> bool:
    import win32gui

    found = False

    def _cb(hwnd, _):
        nonlocal found
        if not win32gui.IsWindowVisible(hwnd):
            return True
        cls = win32gui.GetClassName(hwnd) or ""
        title = (win32gui.GetWindowText(hwnd) or "").strip()
        if cls == "#32770":
            found = True
        elif cls == "CabinetWClass" and _is_valid_cabinet_file_picker(title):
            found = True
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return found


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
        if _native_file_dialog_visible():
            _log("  [upload] file dialog opened")
            return
        raise PcWeChatPublishError("upload click missed; adjust WECHAT_UPLOAD_ABS_X/Y")

    points = _upload_points_for_window(win)
    for x_ratio, y_ratio in points:
        _log(f"  [upload] click upload zone ({x_ratio:.2f}, {y_ratio:.2f})")
        _rect_click(win, x_ratio, y_ratio)
        time.sleep(0.5)
        if _native_file_dialog_visible():
            hwnd = _pick_file_dialog_hwnd()
            title = _win32_window_text(hwnd) if hwnd else ""
            _log(f"  [upload] file dialog opened hwnd={hwnd} title={title!r}")
            return
        _rect_click(win, x_ratio, y_ratio, double=True)
        time.sleep(0.5)
        if _native_file_dialog_visible():
            hwnd = _pick_file_dialog_hwnd()
            title = _win32_window_text(hwnd) if hwnd else ""
            _log(f"  [upload] file dialog opened (double-click) hwnd={hwnd} title={title!r}")
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
            if _native_file_dialog_visible():
                return

        hints = ("上传时长", "点击上传", "上传视频", "MP4")
        for hint in hints:
            try:
                label = win.child_window(title_re=f".*{hint}.*")
                if label.exists(timeout=0.5):
                    label.click_input()
                    time.sleep(0.5)
                    if _native_file_dialog_visible():
                        return
            except Exception:
                pass

    _upload_coordinate_grid(win, path_str=path_str)


def upload_video(session: PublishSession, video: Path) -> None:
    win = session.window
    if not video.is_file():
        raise PcWeChatPublishError(f"video not found: {video}")
    _dismiss_stray_upload_windows()
    _activate_wechat_app(win)

    if not _is_real_publish_form(win):
        raise PcWeChatPublishError(
            "not on publish form (maybe on video feed). "
            "Open 发表动态 page or run without --skip-nav."
        )

    if _video_slot_filled(session):
        _log("  [upload] skip — video already on publish form")
        return

    path_str = str(video.resolve())
    _log(f"  [upload] {video}")
    try:
        _click_upload_area(win, sparse=session.sparse, path_str=path_str)
        if _native_file_dialog_visible():
            _log("  [upload] submitting path to open dialog...")
            if _submit_path_to_open_dialog(path_str, video):
                _log(f"  [upload] selected: {video.name}")
            else:
                _pick_file_in_dialog(video)
    except PcWeChatPublishError:
        if not _video_slot_filled(session):
            raise
    finally:
        _dismiss_stray_upload_windows()

    if _video_slot_filled(session):
        _log("  [upload] video on form after upload attempt")
        return
    raise PcWeChatPublishError(
        "upload failed: no video on publish form. Close Explorer/WMP windows and rerun."
    )

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


def _maximize_window(win) -> None:
    """Maximize a window so the clipped orange 发表 button (cut off at the
    right edge of a narrow 视频号助手 window) becomes fully visible/clickable."""
    try:
        import win32con
        import win32gui

        hwnd = int(win.handle)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(0.2)
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        time.sleep(0.8)
        _log("  [publish] maximized publish-form window")
    except Exception as exc:
        _log(f"  [publish] maximize failed: {exc}")


def _resolve_form_window(default):
    """Find the window actually showing the publish form (prefer it over the
    main 微信 shell, whose coordinate space does not match the form)."""
    if not _vision_nav_enabled():
        return default
    for w, _, _, _ in iter_wechat_windows():
        geo = _window_geometry(w)
        if not geo or geo[2] < 450:
            continue
        try:
            if _vision_classify_page(w) == "publish_form":
                return w
        except Exception:
            continue
    return default


def _publish_form_still_open(win) -> bool:
    try:
        return _is_real_publish_form(win)
    except Exception:
        return True


def _wait_publish_action_result(win, *, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _publish_form_still_open(win):
            _log("  [publish] form left publish page")
            return True
        time.sleep(0.5)
    return False


def _vision_click_publish_button(win) -> bool:
    if not _vision_nav_enabled():
        return False
    goal = (
        "Click the final orange primary publish/submit button on the WeChat Channels "
        "publish form. It is at the bottom-right edge of the form, to the right of "
        "手机预览 / 保存草稿. If the button has no visible text, click the orange square."
    )
    _log("  [publish] locate publish button (vision LLM)")
    return _vision_click(win, goal)


def click_publish(session: PublishSession) -> None:
    win = session.window
    # The orange 发表 button is often clipped at the right edge of a narrow
    # 视频号助手 window. Resolve the real form window and maximize it so the
    # button is fully visible before we try to click it.
    form_win = _resolve_form_window(win)
    _maximize_window(form_win)
    win = form_win
    _log("  [publish] click publish...")
    if not session.sparse and _publish_button(win, click=True):
        if _wait_publish_action_result(win):
            return
        _log("  [publish] UIA click did not submit; fallback")

    # The final 发表 / 保存草稿 buttons sit at the BOTTOM of the form, usually
    # below the fold (vision: "form needs scrolling down to reach bottom
    # buttons"). Scroll the form down, then locate the orange 发表 button.
    for scroll_round in range(5):
        _log(f"  [publish] scroll form to bottom (round {scroll_round + 1})")
        _scroll_window(win, 0.60, 0.55, -8)
        time.sleep(0.6)
        if _publish_button(win, click=True):
            if _wait_publish_action_result(win):
                return
        if _vision_click_publish_button(win):
            if _wait_publish_action_result(win):
                return
            _log("  [publish] vision click did not submit; keep scrolling")

    # PC WeChat sometimes renders the final submit as a textless orange block clipped
    # on the far-right edge. Default close to that block, not the 手机预览 button.
    for x_ratio, y_ratio in (
        (
            _ratio_env("WECHAT_PUBLISH_X_RATIO", 0.985),
            _ratio_env("WECHAT_PUBLISH_Y_RATIO", 0.76),
        ),
        (0.975, 0.76),
        (0.965, 0.77),
        (0.99, 0.77),
        (0.965, 0.93),
        (0.93, 0.93),
        (0.88, 0.93),
        (0.80, 0.93),
        (0.965, 0.90),
    ):
        _log(f"  [publish] coordinate click publish button ({x_ratio:.3f}, {y_ratio:.3f})")
        _rect_click(win, x_ratio, y_ratio)
        if _wait_publish_action_result(win):
            return

    raise PcWeChatPublishError(
        "publish click did not submit. The form is still open; adjust "
        "WECHAT_PUBLISH_X_RATIO/Y_RATIO to the orange publish button."
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
    _log("  [publish] submitted (publish form left or success confirmed)")
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
    # Mirror to the log the daily pipeline reads (publish_pipeline._read_last_publish_url).
    if payload.get("published"):
        tencent = {
            "title": payload.get("title") or payload.get("body_preview", "")[:40],
            "video": payload.get("video", ""),
            "platform": "shipinhao_pcwechat",
            "ts": payload.get("ts", ""),
        }
        tpath = _logs_dir() / "last_tencent_publish.json"
        tpath.write_text(
            json.dumps(tencent, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PC WeChat Channels UI publish (test)")
    parser.add_argument("--video", type=Path, help="MP4 path (default: --latest)")
    parser.add_argument("--latest", action="store_true", help="latest archive zh video")
    parser.add_argument("--locale", default="zh", choices=("zh", "en"))
    parser.add_argument("--desc", default="", help="override description")
    parser.add_argument("--script", type=Path, help="script json for caption fields")
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

    script_override = None
    if args.script:
        script_override = args.script if args.script.is_absolute() else (ROOT / args.script)
        script_override = script_override.resolve()

    fields = resolve_publish_fields(
        video,
        pack_dir=pack_dir,
        desc_override=args.desc,
        script_override=script_override,
    )
    body = build_publish_body(fields)

    _log("== PC WeChat Channels publish (test) == [build:2026-06-30j maximize-publish]")
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
        result["title"] = fields.get("title") or video.stem
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
