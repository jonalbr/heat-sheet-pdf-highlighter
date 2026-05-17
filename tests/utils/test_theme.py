import sys
import types
import builtins

import pytest

from src.utils import theme


class FakeWinregKey:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class FakeWinregModule(types.SimpleNamespace):
    def OpenKey(self, key, path):
        return FakeWinregKey()

    def QueryValueEx(self, key, value_name):
        if value_name == theme.WINDOWS_THEME_VALUE:
            return self.value, None
        raise OSError("Unknown registry value")


class FakeTkWindow:
    def __init__(self, hwnd=123):
        self.hwnd = hwnd

    def winfo_id(self):
        return self.hwnd


@pytest.fixture(autouse=True)
def patch_os_name(monkeypatch):
    monkeypatch.setattr(theme, "os", types.SimpleNamespace(name="nt"))
    yield


def test_get_windows_app_theme_light(monkeypatch):
    fake_winreg = FakeWinregModule(HKEY_CURRENT_USER="HKCU", value=1)
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    assert theme.get_windows_app_theme() == "light"


def test_get_windows_app_theme_dark(monkeypatch):
    fake_winreg = FakeWinregModule(HKEY_CURRENT_USER="HKCU", value=0)
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    assert theme.get_windows_app_theme() == "dark"


def test_get_windows_app_theme_returns_none_on_error(monkeypatch):
    fake_winreg = FakeWinregModule(HKEY_CURRENT_USER="HKCU", value="bad")
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    assert theme.get_windows_app_theme() is None


def test_get_windows_app_theme_returns_none_when_winreg_missing(monkeypatch):
    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "winreg":
            raise ImportError("missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delitem(sys.modules, "winreg", raising=False)

    assert theme.get_windows_app_theme() is None


def test_get_windows_app_theme_returns_none_on_registry_oserror(monkeypatch):
    class FailingWinreg(types.SimpleNamespace):
        def OpenKey(self, key, path):
            raise OSError("registry unavailable")

    monkeypatch.setitem(sys.modules, "winreg", FailingWinreg(HKEY_CURRENT_USER="HKCU"))

    assert theme.get_windows_app_theme() is None


def test_get_effective_theme_system_falls_back_to_light(monkeypatch):
    monkeypatch.setattr(theme, "os", types.SimpleNamespace(name="posix"))
    assert theme.get_effective_theme("system") == "light"


def test_get_effective_theme_returns_dark_or_light():
    assert theme.get_effective_theme("dark") == "dark"
    assert theme.get_effective_theme("light") == "light"
    assert theme.get_effective_theme("unknown") == "light"


def test_get_theme_colors_falls_back_to_light():
    assert theme.get_theme_colors("unknown") == theme.THEME_COLORS["light"]


def test_theme_colors_have_distinct_text_and_background():
    for colors in theme.THEME_COLORS.values():
        assert colors.foreground != colors.background
        assert colors.tooltip_foreground != colors.tooltip_background


def test_get_windows_title_bar_hwnd_prefers_parent_handle(monkeypatch):
    fake_windll = types.SimpleNamespace(user32=types.SimpleNamespace(GetParent=lambda hwnd: 456))
    monkeypatch.setattr(theme.ctypes, "windll", fake_windll, raising=False)

    assert theme._get_windows_title_bar_hwnd(FakeTkWindow()) == 456


def test_get_windows_title_bar_hwnd_falls_back_to_window_handle(monkeypatch):
    fake_windll = types.SimpleNamespace(user32=types.SimpleNamespace(GetParent=lambda hwnd: 0))
    monkeypatch.setattr(theme.ctypes, "windll", fake_windll, raising=False)

    assert theme._get_windows_title_bar_hwnd(FakeTkWindow()) == 123


def test_set_windows_title_bar_theme_calls_dwm(monkeypatch):
    calls = {}

    class FakeDwmapi:
        def DwmSetWindowAttribute(self, hwnd, attribute, value, size):
            calls["args"] = (hwnd, attribute, value._obj.value, size)
            return 0

    fake_windll = types.SimpleNamespace(
        user32=types.SimpleNamespace(GetParent=lambda hwnd: 456),
        dwmapi=FakeDwmapi(),
    )
    monkeypatch.setattr(theme.ctypes, "windll", fake_windll, raising=False)

    assert theme.set_windows_title_bar_theme(FakeTkWindow(), "dark") is True
    assert calls["args"][:3] == (456, theme.DWMWA_USE_IMMERSIVE_DARK_MODE, 1)


def test_set_windows_title_bar_theme_disables_dark_mode_for_light(monkeypatch):
    values = []

    class FakeDwmapi:
        def DwmSetWindowAttribute(self, hwnd, attribute, value, size):
            values.append(value._obj.value)
            return 0

    fake_windll = types.SimpleNamespace(
        user32=types.SimpleNamespace(GetParent=lambda hwnd: 0),
        dwmapi=FakeDwmapi(),
    )
    monkeypatch.setattr(theme.ctypes, "windll", fake_windll, raising=False)

    assert theme.set_windows_title_bar_theme(FakeTkWindow(), "light") is True
    assert values == [0]


def test_set_windows_title_bar_theme_is_noop_off_windows(monkeypatch):
    monkeypatch.setattr(theme, "os", types.SimpleNamespace(name="posix"))

    assert theme.set_windows_title_bar_theme(FakeTkWindow(), "dark") is False


def test_set_windows_title_bar_theme_returns_false_on_platform_error(monkeypatch):
    fake_windll = types.SimpleNamespace(
        user32=types.SimpleNamespace(GetParent=lambda hwnd: (_ for _ in ()).throw(OSError("boom")))
    )
    monkeypatch.setattr(theme.ctypes, "windll", fake_windll, raising=False)

    assert theme.set_windows_title_bar_theme(FakeTkWindow(), "dark") is False
