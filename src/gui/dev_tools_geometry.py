"""Monitor-aware placement for the Dev Tools window without moving its parent."""

from dataclasses import dataclass
import ctypes
from ctypes import wintypes
import os
from tkinter import TclError


@dataclass(frozen=True)
class Bounds:
    """An outer window or work-area rectangle in screen coordinates."""

    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self):
        return max(1, self.right - self.left)

    @property
    def height(self):
        return max(1, self.bottom - self.top)


def beside_parent(parent: Bounds, size: tuple[int, int], work: Bounds, gap=16) -> Bounds:
    """Prefer right, then left, otherwise overlap within the available work area."""
    width, height = min(size[0], work.width), min(size[1], work.height)
    right = parent.right + gap
    left = parent.left - gap - width
    if right >= work.left and right + width <= work.right:
        x = right
    elif left >= work.left and left + width <= work.right:
        x = left
    else:
        x = max(work.left, min(right, work.right - width))
    y = max(work.top, min(parent.top + (parent.height - height) // 2, work.bottom - height))
    return Bounds(x, y, x + width, y + height)


def clamp_bounds(bounds: Bounds, work: Bounds) -> Bounds:
    """Keep an existing position when possible, with its complete frame on screen."""
    width, height = min(bounds.width, work.width), min(bounds.height, work.height)
    x = max(work.left, min(bounds.left, work.right - width))
    y = max(work.top, min(bounds.top, work.bottom - height))
    return Bounds(x, y, x + width, y + height)


def _user32():
    """Declare pointer-sized signatures for the Windows geometry functions."""
    api = ctypes.windll.user32
    api.GetParent.argtypes = [wintypes.HWND]
    api.GetParent.restype = wintypes.HWND
    api.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    api.MonitorFromWindow.restype = wintypes.HANDLE
    api.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    api.GetMonitorInfoW.restype = wintypes.BOOL
    for name in ("GetWindowRect", "GetClientRect"):
        function = getattr(api, name)
        function.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        function.restype = wintypes.BOOL
    api.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    api.SetWindowPos.restype = wintypes.BOOL
    return api


def _hwnd(window, api):
    child = window.winfo_id()
    return api.GetParent(child) or child


def window_bounds(window) -> Bounds:
    """Read the native outer frame, falling back to Tk client coordinates."""
    if os.name == "nt":
        try:
            api = _user32()
            rect = wintypes.RECT()
            if api.GetWindowRect(_hwnd(window, api), ctypes.byref(rect)):
                return Bounds(rect.left, rect.top, rect.right, rect.bottom)
        except AttributeError, OSError, TclError:
            pass
    x, y = window.winfo_rootx(), window.winfo_rooty()
    return Bounds(x, y, x + window.winfo_width(), y + window.winfo_height())


def frame_padding(window) -> tuple[int, int]:
    """Measure decorations so sizing includes the title bar and borders."""
    if os.name == "nt":
        try:
            api = _user32()
            outer, inner = wintypes.RECT(), wintypes.RECT()
            hwnd = _hwnd(window, api)
            if api.GetWindowRect(hwnd, ctypes.byref(outer)) and api.GetClientRect(hwnd, ctypes.byref(inner)):
                return (
                    max(0, outer.right - outer.left - inner.right + inner.left),
                    max(0, outer.bottom - outer.top - inner.bottom + inner.top),
                )
        except AttributeError, OSError, TclError:
            pass
    return 0, 0


def monitor_work_area(parent) -> Bounds:
    """Use the parent's monitor work area, including negative monitor origins."""
    if os.name == "nt":

        class MonitorInfo(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD),
                ("monitor", wintypes.RECT),
                ("work", wintypes.RECT),
                ("flags", wintypes.DWORD),
            ]

        try:
            api = _user32()
            info = MonitorInfo()
            info.size = ctypes.sizeof(info)
            monitor = api.MonitorFromWindow(_hwnd(parent, api), 2)
            if monitor and api.GetMonitorInfoW(monitor, ctypes.byref(info)):
                rect = info.work
                return Bounds(rect.left, rect.top, rect.right, rect.bottom)
        except AttributeError, OSError, TclError:
            pass
    x, y = parent.winfo_vrootx(), parent.winfo_vrooty()
    return Bounds(x, y, x + parent.winfo_screenwidth(), y + parent.winfo_screenheight())


def apply_bounds(window, bounds: Bounds, padding: tuple[int, int]):
    """Apply client size and outer-frame position before mapping the window."""
    width, height = max(1, bounds.width - padding[0]), max(1, bounds.height - padding[1])
    window.geometry(f"{width}x{height}+{bounds.left}+{bounds.top}")
    window.update_idletasks()
    if os.name == "nt":
        try:
            api = _user32()
            # Set the complete frame explicitly; Tk geometry can offset a hidden title bar.
            api.SetWindowPos(
                _hwnd(window, api), None, bounds.left, bounds.top, bounds.width, bounds.height, 0x0014
            )  # NOZORDER | NOACTIVATE
            window.update_idletasks()
        except AttributeError, OSError, TclError:
            pass
