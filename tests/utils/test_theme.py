import sys
import types

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
