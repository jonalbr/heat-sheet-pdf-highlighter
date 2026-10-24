"""Exercise reset with actual Tk variables, traces and open dialog drafts."""

import json
import tkinter as tk

import pytest
from pymupdf import Document

from src.config.paths import Paths
from src.gui import main_window
from src.gui.dev_tools import DevToolsWindow
from src.gui.main_window import PDFHighlighterApp, ProcessingJob
from src.models import MarkingMode


class Preview:
    """Keep scheduled preview requests observable without native subprocesses."""

    def __init__(self, scheduler):
        self.requests = []
        self.cleanup_worker = None
        self.on_result = self.on_error = None
        self.last_result = self.last_request = None
        self._closed = False

    def warm(self):
        pass

    def request(self, request, **kwargs):
        self.requests.append(request)

    def detach(self):
        self.on_result = self.on_error = None

    def subscribe(self, on_result, on_error):
        self.on_result, self.on_error = on_result, on_error

    def close(self):
        pass

    def set_paused(self, value):
        pass


@pytest.fixture
def app(tmp_path, monkeypatch, request):
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(str(exc))
    root.withdraw()
    monkeypatch.setenv("HSPH_SCREENSHOT_MODE", "1")
    monkeypatch.setenv("HSPH_SCREENSHOT_TARGET", "devtools")
    monkeypatch.delenv("HSPH_USE_DEFAULT_SETTINGS", raising=False)
    monkeypatch.delenv("HSPH_FORCE_LANGUAGE", raising=False)
    monkeypatch.delenv("HSPH_FORCE_THEME", raising=False)
    monkeypatch.setattr(Paths, "settings_file", tmp_path / "settings.json")
    if hasattr(request, "param"):
        Paths.settings_file.write_bytes(request.param)
    monkeypatch.setattr(main_window, "MarkingPreview", Preview)
    monkeypatch.setattr(DevToolsWindow, "_populate_releases_for_screenshot", lambda self: None)
    notices = []
    monkeypatch.setattr(main_window, "show_info", lambda app, title, text: notices.append((title, text)))
    instance = PDFHighlighterApp(root)
    instance.notices = notices
    yield instance
    instance.close()
    for timer in root.tk.call("after", "info"):
        root.after_cancel(timer)
    root.destroy()


def test_reset_synchronizes_every_setting_draft_and_next_job(app, tmp_path, monkeypatch):
    changes = {
        "search_str": "Changed", "mark_only_relevant_lines": 0, "names": "Alice",
        "marking_mode": "named_matches", "highlight_color_default": "#123456",
        "highlight_color_names": "#654321", "language": "en", "theme_mode": "dark",
        "watermark_enabled": "True", "watermark_text": "Saved watermark", "watermark_size": 40,
        "watermark_color": "#000000", "watermark_position": "custom",
        "watermark_x_ratio": 0.2, "watermark_y_ratio": 0.8, "update_channel": "rc",
        "verify_sha": "False", "ask_for_update": "False", "update_available": "True",
        "newest_version_available": "99.0.0", "update_cache_ttl_seconds": 25,
        "releases_cache_ttl_seconds": 30, "ocr_enabled": "False", "ocr_language": "eng",
        "ocr_dpi": 72, "ocr_reduce_large_outputs": "False",
    }
    app.app_settings.update_settings(changes)
    app.sync_settings_to_ui()
    source = tmp_path / "selected.pdf"
    with Document() as document:
        document.new_page()
        document.save(source)
    app.input_file_full_path = str(source)
    app.pdf_file_var.set(source.name)
    app.ocr_detection_path, app.ocr_detection_result = str(source), True
    app.dev_tools.open()
    app.filter_dialog.open()
    app.watermark_dialog.open()
    app.filter_dialog.names_entry.insert("end", ", Uncommitted")
    app.watermark_dialog._draft_vars["text"].set("Uncommitted watermark")
    app.preview_window_handler.window = tk.Toplevel(app.root)
    app.preview_window_handler.last_watermark_data = {"text": "Old preview"}
    app.preview_window_handler.last_origin = app.watermark_dialog.window

    saved = []
    save = app.app_settings.save_settings

    def counted_save():
        saved.append(app.app_settings.snapshot())
        return save()

    monkeypatch.setattr(app.app_settings, "save_settings", counted_save)
    monkeypatch.setattr("src.gui.dev_tools.ask_ok_cancel", lambda *args: True)
    app.dev_tools._reset_settings()
    defaults = app.app_settings.default_settings
    assert app.app_settings.snapshot() == defaults
    assert saved == [defaults]
    assert json.loads(app.app_settings.settings_file.read_text(encoding="utf-8")) == defaults
    for key, attribute in {
        "search_str": "search_phrase_var", "mark_only_relevant_lines": "relevant_lines_var",
        "names": "names_var", "marking_mode": "marking_mode_var",
        "highlight_color_default": "highlight_color_default_var",
        "highlight_color_names": "highlight_color_names_var", "language": "language_var",
        "theme_mode": "theme_mode_var",
    }.items():
        assert getattr(app, attribute).get() == defaults[key]
    assert app.dev_tools.channel_var.get() == "stable"
    assert app.dev_tools.sha_required.get() is True
    assert app._current_effective_theme in ("light", "dark")
    assert app.strings["btn_start"] == "Start"
    assert app.filter_dialog.window is None
    assert app.watermark_dialog.window is None
    assert not app.preview_window_handler.is_open()
    assert app.preview_window_handler.last_watermark_data == {}
    assert app.preview_window_handler.last_origin is None
    # Delayed callbacks from discarded dialogs must not reapply old drafts.
    app.filter_dialog.apply_changes()
    app.watermark_dialog.apply_changes()
    assert saved == [defaults]
    assert app.input_file_full_path == str(source)
    assert app.pdf_file_var.get() == source.name
    options = app._collect_processing_options()
    assert options.search_str == defaults["search_str"]
    assert options.only_relevant and options.names == ()
    assert options.marking_mode == MarkingMode.ALL_MATCHES
    assert options.highlight_color_default == defaults["highlight_color_default"]
    assert options.highlight_color_names == defaults["highlight_color_names"]
    assert dict(options.watermark) == {key: value for key, value in defaults.items() if key.startswith("watermark_")}
    assert options.ocr_enabled and options.ocr_language == "deu" and options.ocr_dpi == 300
    assert options.ocr_reduce_large_outputs and options.needs_ocr is True
    app._prefetch_preview()
    assert app.marking_preview.requests[-1].names == ()
    assert app.marking_preview.requests[-1].search_str == defaults["search_str"]
    app.filter_dialog.open()
    app.watermark_dialog.open()
    assert app.filter_dialog._draft()["names"] == ""
    assert app.watermark_dialog._draft()["text"] == ""


@pytest.mark.parametrize("operation", ["job", "cancelled_job", "download", "cancelled_download", "reserved_download"])
def test_reset_is_blocked_before_prompt_and_after_nested_confirmation(app, monkeypatch, operation):
    app.app_settings.update_setting("names", "Keep")
    before = app.app_settings.settings_file.read_bytes()

    def reserve():
        if operation in ("job", "cancelled_job"):
            app._job = ProcessingJob(1, "selected.pdf", app._collect_processing_options())
            if operation == "cancelled_job":
                app._job.cancel.set()
        elif operation == "reserved_download":
            app.update_checker._active_download = True
        else:
            app.start_download()
            if operation == "cancelled_download":
                app.abort_download()

    def confirm(*args):
        reserve()
        return True

    monkeypatch.setattr("src.gui.dev_tools.ask_ok_cancel", confirm)
    app.dev_tools._reset_settings()
    assert app.app_settings.snapshot()["names"] == "Keep"
    monkeypatch.setattr("src.gui.dev_tools.ask_ok_cancel", lambda *args: pytest.fail("Busy reset prompted"))
    app.dev_tools._reset_settings()
    app.reset_settings()
    assert app.app_settings.settings_file.read_bytes() == before
    app._job = None
    app.update_checker._active_download = False
    app.download_active = False


def test_reset_write_failure_updates_gui_and_explains_session_only(app, monkeypatch):
    app.app_settings.update_setting("names", "Before")
    app.sync_settings_to_ui()
    before = app.app_settings.settings_file.read_bytes()

    def denied(*args):
        raise PermissionError("replace denied")

    monkeypatch.setattr("src.config.settings.os.replace", denied)
    app.reset_settings()
    assert app.names_var.get() == ""
    assert app.app_settings.snapshot() == app.app_settings.default_settings
    assert app.app_settings.settings_file.read_bytes() == before
    app._show_settings_notices()
    assert len(app.notices) == 1
    assert str(app.app_settings.settings_file) in app.notices[0][1]
    app._show_settings_notices()
    assert len(app.notices) == 1


@pytest.mark.parametrize("app", [b"\xff"], indirect=True)
def test_recovery_notice_is_translated_and_only_shown_after_gui_start(app, tmp_path):
    assert app.notices == []
    # This runs the real after_idle callback registered during initialization.
    app.root.update_idletasks()
    assert len(app.notices) == 1
    assert "Sicherung" in app.notices[0][1]
    backup, = tmp_path.glob("settings.json.corrupt-*.bak")
    assert backup.read_bytes() == b"\xff"
    assert str(backup) in app.notices[0][1]
    app._poll_work()
    assert len(app.notices) == 1


@pytest.mark.parametrize("dialog_name", ["filter_dialog", "watermark_dialog"])
def test_dialog_apply_saves_once_with_real_app_traces(app, monkeypatch, dialog_name):
    dialog = getattr(app, dialog_name)
    dialog.open()
    if dialog_name == "filter_dialog":
        dialog.mode.set("distinguish_names")
        dialog.names_entry.insert("end", "Alice")
        dialog.colors["highlight_color_names"].set("#abcdef")
    else:
        dialog._draft_vars["enabled"].set(1)
        dialog._draft_vars["text"].set("Müller")
        dialog._draft_vars["size"].set("24")
    writes = []
    save = app.app_settings.save_settings

    def record():
        writes.append(app.app_settings.snapshot())
        return save()

    monkeypatch.setattr(app.app_settings, "save_settings", record)
    dialog.apply_changes()
    assert len(writes) == 1
    options = app._collect_processing_options()
    if dialog_name == "filter_dialog":
        assert options.names == ("Alice",)
        assert options.marking_mode == MarkingMode.DISTINGUISH_NAMES
        assert options.highlight_color_names == "#ABCDEF"
        assert app.names_var.get() == "Alice"
    else:
        assert dict(options.watermark)["watermark_text"] == "Müller"
        assert dict(options.watermark)["watermark_size"] == 24


def test_cancelled_reset_retains_open_drafts(app, monkeypatch):
    app.filter_dialog.open()
    app.watermark_dialog.open()
    app.filter_dialog.mode.set("distinguish_names")
    app.filter_dialog.names_entry.insert("end", "Keep draft")
    app.watermark_dialog._draft_vars["text"].set("Keep watermark draft")
    before = app.app_settings.settings_file.read_bytes()
    monkeypatch.setattr("src.gui.dev_tools.ask_ok_cancel", lambda *args: False)
    app.dev_tools._reset_settings()
    assert app.filter_dialog._draft()["names"] == "Keep draft"
    assert app.watermark_dialog._draft()["text"] == "Keep watermark draft"
    assert app.app_settings.settings_file.read_bytes() == before
