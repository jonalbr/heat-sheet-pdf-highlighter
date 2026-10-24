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
        self.closed = False

    def request(self, request):
        self.requests.append(request)

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
    instance.window.withdraw()
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
    assert dialog.preview_note.winfo_manager() == ""
    dialog._show_preview(result)
    assert dialog.preview_note.winfo_manager() == "pack"
    dialog.mode.set("all_matches")
    assert dialog.preview_note.winfo_manager() == ""
    dialog._show_preview(PreviewResult(png.getvalue(), 1))
    assert dialog.preview_note.winfo_manager() == ""
