"""关闭 Windows 原生「打开」文件选择对话框（Playwright 未拦截时会残留）。"""

from __future__ import annotations


def dismiss_native_open_dialogs() -> int:
    """关闭桌面上残留的「打开 / Open」文件选择对话框。返回关闭数量。非 Windows 直接返回 0。"""
    try:
        import win32con
        import win32gui
    except ImportError:
        return 0

    closed = 0

    def _cb(hwnd, _):
        nonlocal closed
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = (win32gui.GetWindowText(hwnd) or "").strip()
            cls = win32gui.GetClassName(hwnd) or ""
            if cls != "#32770":
                return True
            if not any(
                k in title
                for k in ("打开", "Open", "选择文件", "选择要上传", "Browse")
            ):
                return True
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            closed += 1
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return closed
