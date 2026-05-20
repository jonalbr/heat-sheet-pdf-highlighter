import os
import sys
import pytest

from src.config.paths import Paths, _get_bundle_dir
from src.constants import APP_NAME
from pathlib import Path as _Path


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific behavior for APPDATA")
def test_get_settings_path_windows_uses_appdata(tmp_path, monkeypatch):
    fake_appdata = tmp_path / "AppData" / "Roaming"
    monkeypatch.setenv("APPDATA", str(fake_appdata))
    p = Paths.get_settings_path()
    assert p == fake_appdata / APP_NAME
    assert p.exists() and p.is_dir()


@pytest.mark.skipif(os.name != "nt", reason="Windows-specific behavior for APPDATA")
def test_get_settings_path_windows_missing_appdata(monkeypatch):
    # Remove APPDATA entirely
    if "APPDATA" in os.environ:
        monkeypatch.delenv("APPDATA", raising=False)
    with pytest.raises(RuntimeError):
        Paths.get_settings_path()


def test_is_valid_path_success(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hi", encoding="utf-8")
    assert Paths.is_valid_path(str(f)) == str(f)


def test_is_valid_path_empty():
    with pytest.raises(ValueError):
        Paths.is_valid_path("")


def test_is_valid_path_not_exists(tmp_path):
    with pytest.raises(FileNotFoundError):
        Paths.is_valid_path(str(tmp_path / "missing.txt"))


def test_is_valid_path_directory(tmp_path):
    d = tmp_path / "folder"
    d.mkdir()
    with pytest.raises(ValueError):
        Paths.is_valid_path(str(d))


def test_get_settings_path_posix_mac(tmp_path, monkeypatch):
    # Cover macOS branch
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(_Path, "home", lambda: tmp_path)
    p = Paths.get_settings_path()
    assert p == tmp_path / f"Library/Application Support/{APP_NAME}"
    assert p.exists()


def test_get_settings_path_posix_linux(tmp_path, monkeypatch):
    # Cover Linux branch
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(_Path, "home", lambda: tmp_path)
    p = Paths.get_settings_path()
    assert p == tmp_path / f".config/{APP_NAME}"
    assert p.exists()


def test_get_settings_path_unsupported_os(monkeypatch):
    monkeypatch.setattr(os, "name", "weirdos")
    with pytest.raises(Exception):
        Paths.get_settings_path()


def test_get_bundle_dir_for_source_tree(tmp_path):
    module_file = tmp_path / "src" / "config" / "paths.py"

    assert _get_bundle_dir(frozen=False, module_file=module_file) == tmp_path


def test_get_bundle_dir_for_pyinstaller(tmp_path):
    unpacked_root = tmp_path / "_MEI123"

    assert _get_bundle_dir(frozen=True, meipass=unpacked_root) == unpacked_root


def test_get_bundle_dir_reads_sys_meipass_when_not_passed(tmp_path, monkeypatch):
    unpacked_root = tmp_path / "_MEI456"
    monkeypatch.setattr(sys, "_MEIPASS", str(unpacked_root), raising=False)

    assert _get_bundle_dir(frozen=True, meipass=None) == unpacked_root


def test_get_bundle_dir_for_cx_freeze(tmp_path):
    executable = tmp_path / "installed" / "heat_sheet_pdf_highlighter.exe"

    assert _get_bundle_dir(frozen=True, meipass=None, executable=executable) == executable.parent


def test_ocr_tessdata_path_lives_under_assets():
    assert Paths.ocr_tessdata_dir == Paths.bundle_dir / "assets" / "ocr" / "tessdata"
