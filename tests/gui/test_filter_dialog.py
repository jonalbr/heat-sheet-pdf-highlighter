"""Check the real Tk dialog with an isolated preview scheduler."""

import json
from io import BytesIO
from types import SimpleNamespace
import tkinter as tk

import pytest
from PIL import Image

from src.config.settings import AppSettings
from src.core.marking_preview import PreviewResult
from src.gui import filter_dialog
from src.gui.ui_strings import build_strings
from src.utils.localization import setup_translation


class Preview:
    def __init__(self, *args):
        self.requests = []
        self.immediate = []
        self.on_result = args[1]
        self.on_error = args[2]
        self.closed = False

    def request(self, request, *, immediate=False):
        self.requests.append(request)
        self.immediate.append(immediate)

    def close(self):
        self.closed = True


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("Tk display unavailable")
    root.withdraw()
    settings = AppSettings(tmp_path / "settings.json")
    app = SimpleNamespace(
        root=root,
        strings=build_strings(lambda text: text),
        app_settings=settings,
        names_var=tk.StringVar(root, value="Alice"),
        marking_mode_var=tk.StringVar(root, value="distinguish_names"),
        highlight_color_default_var=tk.StringVar(root, value="#FFFFA6"),
        highlight_color_names_var=tk.StringVar(root, value="#C4FAF8"),
        search_phrase_var=tk.StringVar(root, value="CLUB"),
        relevant_lines_var=tk.IntVar(root, value=1),
        apply_theme_to_window=lambda window: None,
    )
    monkeypatch.setattr(filter_dialog, "MarkingPreview", Preview)
    instance = filter_dialog.FilterDialog(app)
    instance.open()
    yield instance
    instance.close()
    root.destroy()


@pytest.mark.parametrize(
    "mode, default_active, names_active",
    [
        ("all_matches", True, False),
        ("distinguish_names", True, True),
        ("named_matches", False, True),
    ],
)
def test_mode_controls_and_tooltips(dialog, mode, default_active, names_active):
    dialog.mode.set(mode)
    assert str(dialog.names_entry["state"]) == ("normal" if names_active else "disabled")
    for key, active in (("highlight_color_default", default_active), ("highlight_color_names", names_active)):
        label, swatches, custom = dialog.color_widgets[key]
        assert str(custom["state"]) == ("normal" if active else "disabled")
        assert label._hsph_tooltip.text == ("" if active else "Currently not used")
        assert all(str(button["state"]) == ("normal" if active else "disabled") for button, _ in swatches)
    assert dialog.colors["highlight_color_names"].get() == "#C4FAF8"
    assert dialog.names_entry.get("1.0", "end-1c") == "Alice"
    if not names_active:
        _, swatches, _ = dialog.color_widgets["highlight_color_names"]
        # Keep the current selected color visible but desaturate inactive presets.
        assert swatches[0][0]["background"] == "#C4FAF8"
        gray = str(swatches[1][0]["background"])
        assert gray[1:3] == gray[3:5] == gray[5:7]


def test_cancel_discards_every_edit_and_closes_worker(dialog):
    before = dialog.app.app_settings.settings_file.read_bytes()
    worker = dialog._preview
    dialog.colors["highlight_color_names"].set("#AABBCC")
    dialog.mode.set("named_matches")
    dialog.names_entry.insert("end", ", Bob")
    dialog.close()
    assert worker.closed
    assert dialog.app.app_settings.settings_file.read_bytes() == before
    assert dialog.app.names_var.get() == "Alice"
    assert dialog.app.marking_mode_var.get() == "distinguish_names"


def test_apply_publishes_draft_together(dialog):
    dialog.mode.set("named_matches")
    dialog.colors["highlight_color_names"].set("#AABBCC")
    dialog.names_entry.insert("end", ", Bob")
    dialog.apply_changes()
    data = json.loads(dialog.app.app_settings.settings_file.read_text())
    assert data["marking_mode"] == "named_matches"
    assert data["highlight_color_names"] == "#AABBCC"
    assert dialog.app.names_var.get() == "Alice, Bob"


def test_empty_names_rejected_but_preview_allowed(dialog, monkeypatch):
    errors = []
    monkeypatch.setattr(filter_dialog, "show_error", lambda *args: errors.append(args))
    dialog.names_entry.delete("1.0", "end")
    dialog._changed()
    assert dialog._preview.requests[-1].names == ()
    dialog.apply_changes()
    assert errors
    assert dialog.window is not None


def test_language_refresh_preserves_uncommitted_draft(dialog):
    dialog.colors["highlight_color_names"].set("#AABBCC")
    dialog.names_entry.insert("end", ", Bob")
    worker = dialog._preview
    assert dialog._preview.requests[-1].example_text.lane == "Lane"
    gettext_de, _ = setup_translation("de")
    dialog.app.strings = build_strings(gettext_de)
    dialog.refresh_ui_strings()
    dialog.window.withdraw()
    assert worker.closed
    assert dialog.colors["highlight_color_names"].get() == "#AABBCC"
    assert dialog.names_entry.get("1.0", "end-1c") == "Alice, Bob"
    assert dialog.app.names_var.get() == "Alice"
    assert dialog._preview.requests[-1].example_text.lane == "Bahn"
    assert dialog._preview.requests[-1].example_text.heading == "Lauf 1/3 - ca. 10:30 Uhr"


def test_cancel_color_picker_leaves_selection(dialog, monkeypatch):
    monkeypatch.setattr(filter_dialog.colorchooser, "askcolor", lambda **kwargs: (None, None))
    dialog._choose_color("highlight_color_names")
    assert dialog.colors["highlight_color_names"].get() == "#C4FAF8"


def test_external_window_destroy_releases_process_and_traces(dialog):
    worker = dialog._preview
    dialog.window.destroy()
    assert worker.closed
    assert dialog.window is None
    assert dialog._external_traces == []


def test_missing_name_note_is_conditional_and_clears_when_settings_change(dialog):
    png = BytesIO()
    Image.new("RGB", (8, 8), "white").save(png, format="PNG")
    result = PreviewResult(png.getvalue(), 1, no_named_matches=True)
    assert dialog.preview_note.cget("text") == ""
    dialog._show_preview(result)
    assert dialog.preview_note.cget("text") == dialog._text("flt_no_names_found")
    dialog.mode.set("all_matches")
    assert dialog.preview_note.cget("text") == ""
    dialog._show_preview(PreviewResult(png.getvalue(), 1))
    assert dialog.preview_note.cget("text") == ""



def test_busy_apply_guard_does_not_even_read_draft():
    instance = filter_dialog.FilterDialog(SimpleNamespace(output_options_locked=True))
    instance._draft = lambda: pytest.fail("Read busy draft")
    instance.apply_changes()


def test_busy_preserves_draft_and_mode_states_across_language_change(dialog):
    dialog.names_entry.insert("end", ", Bob")
    draft = dialog._draft()
    dialog.app.output_options_locked = True
    dialog.set_busy(True)
    assert str(dialog.names_entry["state"]) == "disabled"
    dialog.apply_changes()
    assert dialog.app.names_var.get() == "Alice"
    dialog.refresh_ui_strings()
    assert dialog._draft() == draft
    assert str(dialog.names_entry["state"]) == "disabled"
    dialog.app.output_options_locked = False
    dialog.set_busy(False)
    assert str(dialog.names_entry["state"]) == "normal"
    assert dialog._draft() == draft



def preview_result(width=560, height=150, *, no_named_matches=False):
    png = BytesIO()
    Image.new("RGB", (width, height), "white").save(png, format="PNG")
    return PreviewResult(png.getvalue(), 1, no_named_matches=no_named_matches)


def test_dialog_waits_for_first_image_and_requests_it_without_typing_delay(dialog):
    assert dialog.window.state() == "withdrawn"
    assert dialog._preview.immediate == [True]
    dialog._preview.on_result(preview_result())
    dialog.window.update_idletasks()
    assert dialog.window.state() == "normal"
    assert dialog.preview_image.cget("image")
    assert dialog.window.grab_current() is dialog.window
    dialog.colors["highlight_color_default"].set("#123456")
    assert dialog._preview.immediate[-1] is False


def test_preview_image_and_note_changes_do_not_resize_dialog(dialog):
    dialog._show_preview(preview_result())
    dialog.window.update_idletasks()
    size = (dialog.window.winfo_width(), dialog.window.winfo_height())
    requested = (dialog.window.winfo_reqwidth(), dialog.window.winfo_reqheight())
    for result in (preview_result(80, 10, no_named_matches=True), preview_result(560, 40), preview_result()):
        dialog._show_preview(result)
        dialog.window.update_idletasks()
        assert (dialog.window.winfo_width(), dialog.window.winfo_height()) == size
        assert (dialog.window.winfo_reqwidth(), dialog.window.winfo_reqheight()) == requested
    dialog._changed()
    dialog.window.update_idletasks()
    assert (dialog.window.winfo_width(), dialog.window.winfo_height()) == size


def test_reopen_reuses_matching_preview_before_new_worker_result(dialog):
    dialog._show_preview(preview_result())
    dialog.close()
    dialog.open()
    assert dialog.window.state() == "normal"
    assert dialog.preview_image.cget("image")
    assert dialog._preview.requests  # Invalidate any stale in-flight generation.


def test_reopen_invalidates_cached_image_when_source_changes(dialog, tmp_path):
    path = tmp_path / "source.pdf"
    path.write_bytes(b"original")
    dialog.close()
    dialog.app.input_file_full_path = str(path)
    dialog.open()
    dialog._show_preview(preview_result())
    dialog.close()
    path.write_bytes(b"different document")
    dialog.open()
    assert dialog.window.state() == "withdrawn"
    assert not dialog.preview_image.cget("image")


def test_preview_failure_reveals_usable_dialog_and_late_result_after_close_is_ignored(dialog):
    preview = dialog._preview
    preview.on_error()
    assert dialog.window.state() == "normal"
    assert dialog.preview_note.cget("text") == dialog._text("flt_preview_unavailable")
    dialog.close()
    preview.on_result(preview_result())
    assert dialog.window is None
