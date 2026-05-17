"""
Windows theme helper utilities.

This module exposes helpers for determining whether the current Windows
appearance is light or dark and for resolving effective theme choices when
"system" theme mode is selected.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from tkinter import TclError
from typing import Literal

ThemeMode = Literal["system", "light", "dark"]
EffectiveTheme = Literal["light", "dark"]

WINDOWS_THEME_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
WINDOWS_THEME_VALUE = "AppsUseLightTheme"
DWMWA_USE_IMMERSIVE_DARK_MODE = 20


@dataclass(frozen=True)
class ThemeColors:
    """Resolved colors used by Tk and ttk widgets."""

    background: str
    foreground: str
    field_background: str
    muted_foreground: str
    border: str
    active_background: str
    select_background: str
    select_foreground: str
    tooltip_background: str
    tooltip_foreground: str
    tooltip_border: str


THEME_COLORS = {
    "light": ThemeColors(
        background="#eff0f1",
        foreground="#31363b",
        field_background="#ffffff",
        muted_foreground="#5c5c5c",
        border="#c7cdd1",
        active_background="#d8edf7",
        select_background="#3daee9",
        select_foreground="#ffffff",
        tooltip_background="#ffffff",
        tooltip_foreground="#1f1f1f",
        tooltip_border="#d0d0d0",
    ),
    "dark": ThemeColors(
        background="#31363b",
        foreground="#eff0f1",
        field_background="#2f3336",
        muted_foreground="#c7cdd1",
        border="#4b535a",
        active_background="#3daee9",
        select_background="#3daee9",
        select_foreground="#ffffff",
        tooltip_background="#2f3336",
        tooltip_foreground="#eff0f1",
        tooltip_border="#5a6268",
    ),
}


def get_windows_app_theme() -> EffectiveTheme | None:
    """Return the current Windows app theme preference.

    Returns "light" when the Windows apps appearance is set to light, "dark"
    when set to dark, or None on non-Windows platforms or when the registry
    value cannot be read.
    """
    if os.name != "nt":
        return None

    try:
        import winreg  # type: ignore[import]
    except ImportError:
        return None

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_THEME_KEY) as key:
            value, _ = winreg.QueryValueEx(key, WINDOWS_THEME_VALUE)
            return "light" if int(value) == 1 else "dark"
    except OSError:
        return None
    except ValueError:
        return None


def get_effective_theme(theme_mode: str) -> EffectiveTheme:
    """Resolve the effective Breeze theme to use for the current mode."""
    if theme_mode == "dark":
        return "dark"
    if theme_mode == "system":
        return get_windows_app_theme() or "light"
    return "light"


def get_theme_colors(effective_theme: str) -> ThemeColors:
    """Return UI colors for the resolved theme, falling back to light."""
    return THEME_COLORS.get(effective_theme, THEME_COLORS["light"])


def _get_windows_title_bar_hwnd(window) -> int:
    """Return the native frame HWND for a Tk top-level window."""
    hwnd = int(window.winfo_id())
    parent_hwnd = ctypes.windll.user32.GetParent(hwnd)
    return int(parent_hwnd or hwnd)


def set_windows_title_bar_theme(window, effective_theme: str) -> bool:
    """Best-effort toggle for the native Windows title bar dark mode state."""
    if os.name != "nt":
        return False

    try:
        hwnd = _get_windows_title_bar_hwnd(window)
        enabled = ctypes.c_int(1 if effective_theme == "dark" else 0)
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd,
            DWMWA_USE_IMMERSIVE_DARK_MODE,
            ctypes.byref(enabled),
            ctypes.sizeof(enabled),
        )
    except (AttributeError, OSError, TclError, TypeError, ValueError):
        return False
    return result == 0
