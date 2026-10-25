from types import SimpleNamespace
import tkinter as tk

import pytest
from pymupdf import Document

from src.config.settings import AppSettings
from src.gui.dialogs import WatermarkDialog
from src.gui.main_window import PDFHighlighterApp
from src.gui.preview import PreviewWindow
from src.gui.ui_strings import build_strings
from src.utils.localization import setup_translation


class FakeWindow:
    def __init__(self):
        self.destroyed = False

    def winfo_exists(self):
        return not self.destroyed

    def destroy(self):
        self.destroyed = True


class FakePreview:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_close_can_also_close_preview():
    app = SimpleNamespace(root=object(), preview_window_handler=FakePreview())
    app._render_options = lambda: None
    dialog = WatermarkDialog(app)
    dialog.window = FakeWindow()

    dialog.close(close_preview=True)

    assert app.preview_window_handler.closed
    assert dialog.window is None



def test_language_refresh_keeps_raw_draft_values_without_publishing(monkeypatch):
    app = SimpleNamespace(root=object(), preview_window_handler=FakePreview())
    app._render_options = lambda: None
    dialog = WatermarkDialog(app)
    dialog.window = FakeWindow()
    draft = dict(enabled=1, text="Uncommitted", color="#123456", size="unfinished", position="custom",
                 x_ratio=0.25, y_ratio=0.75)
    dialog._draft_vars = {key: SimpleNamespace(get=lambda value=value: value) for key, value in draft.items()}
    restored = []
    monkeypatch.setattr(dialog, "open", lambda value: restored.append(value))
    dialog.refresh_ui_strings()
    assert restored == [draft]
    assert not app.preview_window_handler.closed


def test_busy_stops_position_repeat_without_discarding_draft():
    dialog = WatermarkDialog(SimpleNamespace(root=object()))
    stopped = []
    dialog._stop_nudge = lambda: stopped.append(True)
    dialog.set_busy(True)
    assert dialog._busy
    assert stopped == [True]



def test_busy_apply_neither_reads_nor_publishes_draft():
    dialog = WatermarkDialog(SimpleNamespace(root=object(), output_options_locked=True))
    dialog._draft = lambda: (_ for _ in ()).throw(AssertionError("Read busy draft"))
    dialog.apply_changes()


def test_apply_publishes_preserved_draft_after_unlock(monkeypatch):
    settings = {}
    app = SimpleNamespace(root=object(), output_options_locked=False,
                          app_settings=SimpleNamespace(update_settings=settings.update))
    app._render_options = lambda: None
    dialog = WatermarkDialog(app)
    dialog._draft_active = True
    draft = dict(enabled=1, text="Draft", color="#123456", size="24", position="custom", x_ratio=0.2, y_ratio=0.3)
    dialog._draft_vars = {key: SimpleNamespace(get=lambda value=value: value) for key, value in draft.items()}
    dialog._stop_nudge = lambda: None
    closed = []
    monkeypatch.setattr(dialog, "close", lambda: closed.append(True))
    dialog.set_busy(True)
    dialog.apply_changes()
    assert settings == {}
    dialog.set_busy(False)
    dialog.apply_changes()
    assert settings == dict(watermark_enabled="True", watermark_text="Draft", watermark_color="#123456", watermark_size=24,
                            watermark_position="custom", watermark_x_ratio=0.2, watermark_y_ratio=0.3)
    assert closed == [True]


@pytest.fixture
def dialog(tmp_path, monkeypatch):
    """Use real variables, bindings, windows and an isolated settings file/PDF."""
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(str(exc))
    root.withdraw()
    settings = AppSettings(tmp_path / "settings.json")
    settings.update_settings(dict(
        watermark_enabled="True", watermark_text="Saved", watermark_color="#123456", watermark_size=24,
        watermark_position="custom", watermark_x_ratio=0.25, watermark_y_ratio=0.75,
    ))
    app = SimpleNamespace(root=root, app_settings=settings, strings=build_strings(lambda text: text),
                          output_options_locked=False, apply_theme_to_window=lambda window: None,
                          current_preview_page=1)
    source = tmp_path / "source.pdf"
    with Document() as document:
        document.new_page(width=400, height=300)
        document.save(source)
    app._render_options = lambda: None
    app.input_file_full_path = str(source)
    app.preview_window_handler = preview = PreviewWindow(app)
    app.preview_watermark = preview.preview_watermark
    app.errors = []
    callback_errors = []
    root.report_callback_exception = lambda *error: callback_errors.append(error)
    monkeypatch.setattr("src.gui.dialogs.show_error", lambda *args: app.errors.append(args[-1]))
    instance = app.watermark_dialog = WatermarkDialog(app)
    instance.open()
    root.update()
    yield instance
    instance.close()
    root.destroy()
    assert callback_errors == []


def test_reopen_focuses_the_same_window_without_reloading_draft(dialog, monkeypatch):
    window = dialog.window
    dialog._draft_vars["text"].set("Uncommitted")
    focused = []
    monkeypatch.setattr(window, "lift", lambda: focused.append("lift"))
    monkeypatch.setattr(window, "focus_set", lambda: focused.append("focus"))
    window.withdraw()
    dialog.open()
    assert dialog.window is window
    assert dialog._draft()["text"] == "Uncommitted"
    assert focused == ["lift", "focus"]
    assert window.state() == "normal"
    assert len([child for child in dialog.parent.winfo_children() if isinstance(child, tk.Toplevel)]) == 1


@pytest.mark.parametrize("stored_color", ["#123456", "#abcdef", "#D4776A", "#416CBE"])
def test_palette_keeps_fixed_favorite_when_saved_or_draft_colors_change(dialog, stored_color):
    def palette_buttons():
        return [control for control in dialog._dependent_controls
                if isinstance(control, tk.Button) and getattr(control, "_hsph_swatch_color", None)
                and control.cget("command")]

    dialog.close()
    dialog.app.app_settings.update_setting("watermark_color", stored_color)
    dialog.open()
    colors = [button._hsph_swatch_color for button in palette_buttons()]
    assert colors[0] == "#416CBE"
    assert len(colors) == len(set(colors))

    selected = colors[-1]
    palette_buttons()[-1].invoke()
    assert dialog._draft()["color"] == selected
    assert dialog.app.app_settings.snapshot()["watermark_color"] == stored_color.upper()
    dialog.app.strings = build_strings(setup_translation("de")[0])
    dialog.refresh_ui_strings()
    assert palette_buttons()[0]._hsph_swatch_color == "#416CBE"
    assert dialog._draft()["color"] == selected

    dialog.apply_changes()
    dialog.open()
    colors = [button._hsph_swatch_color for button in palette_buttons()]
    assert colors[0] == "#416CBE"
    assert dialog.app.app_settings.snapshot()["watermark_color"] == selected
    assert len(colors) == len(set(colors))


def test_language_and_theme_refresh_preserve_invalid_raw_draft_and_preview(dialog):
    dialog.preview_button.invoke()
    preview = dialog.app.preview_window_handler
    preview_window = preview.window
    dialog._draft_vars["text"].set("Entwurf")
    dialog._draft_vars["size"].set("unfinished")
    dialog._draft_vars["color"].set("#incomplete")
    before = dialog.app.app_settings.settings_file.read_bytes()
    draft = dialog._draft()
    old_variables = list(dialog._draft_vars.values())
    dialog.app.strings = build_strings(setup_translation("de")[0])
    dialog.refresh_ui_strings()
    assert dialog._draft() == draft
    assert dialog.position_label.get() == "Frei"
    assert preview.window is preview_window
    assert preview.last_origin is dialog.window
    assert all(not variable.trace_info() for variable in old_variables)
    PDFHighlighterApp._apply_theme_to_open_windows(dialog.app)
    assert dialog._draft() == draft
    assert dialog.app.errors == []
    assert dialog.app.app_settings.settings_file.read_bytes() == before


@pytest.mark.parametrize("position,label", [("top", "Oben"), ("bottom", "Unten"), ("custom", "Frei")])
def test_translated_positions_preserve_stored_keys_and_ratios(dialog, position, label):
    dialog.close()
    dialog.app.app_settings.update_setting("watermark_position", position)
    dialog.app.strings = build_strings(setup_translation("de")[0])
    dialog.open()
    assert dialog.position_label.get() == label
    dialog.refresh_ui_strings()
    dialog.apply_changes()
    snapshot = dialog.app.app_settings.snapshot()
    assert snapshot["watermark_position"] == position
    assert snapshot["watermark_x_ratio"] == 0.25
    assert snapshot["watermark_y_ratio"] == 0.75


def test_translated_menu_stores_internal_position_and_updates_preset(dialog):
    dialog.app.strings = build_strings(setup_translation("de")[0])
    dialog.refresh_ui_strings()
    assert [dialog.position_menu.entrycget(index, "label") for index in range(3)] == ["Oben", "Unten", "Frei"]
    dialog.position_menu.invoke(1)
    assert dialog._draft()["position"] == "bottom"
    assert dialog.position_label.get() == "Unten"
    assert dialog._draft()["x_ratio"] == 0.5
    assert dialog._draft()["y_ratio"] == 0.95


@pytest.mark.parametrize("close_way", ["cancel", "escape", "wm_close", "destroy", "close"])
def test_all_cancel_paths_discard_draft_timers_traces_and_preview(dialog, close_way):
    dialog.preview_button.invoke()
    preview = dialog.app.preview_window_handler
    dialog._draft_vars["text"].set("Discard me")
    variables = list(dialog._draft_vars.values())
    before = dialog.app.app_settings.settings_file.read_bytes()
    dialog.nudge_buttons[0].event_generate("<ButtonPress-1>")
    assert dialog.parent.tk.call("after", "info")
    if close_way == "cancel":
        dialog._cancel_button.invoke()
    elif close_way == "escape":
        dialog.window.focus_force()
        dialog.parent.update()
        dialog.window.event_generate("<Escape>")
    elif close_way == "wm_close":
        dialog.parent.tk.call(dialog.window.protocol("WM_DELETE_WINDOW"))
    elif close_way == "destroy":
        dialog.window.destroy()
    else:
        dialog.close()
    dialog.parent.update()
    assert dialog.window is None
    assert dialog._draft_vars == {}
    assert not dialog._draft_active
    assert not dialog.parent.tk.call("after", "info")
    assert all(not variable.trace_info() for variable in variables)
    assert preview.window is None
    assert preview.last_watermark_data == {}
    assert preview.last_origin is None
    dialog.apply_changes()  # Stale commands cannot republish abandoned edits.
    assert dialog.app.app_settings.settings_file.read_bytes() == before
    dialog.close()
    dialog.open()
    assert dialog._draft()["text"] == "Saved"


@pytest.mark.parametrize("field,value", [
    ("size", ""), ("size", "0"), ("size", "101"), ("size", "-1"), ("size", "1.5"),
    ("size", "nan"), ("size", "²"), ("color", "#"), ("color", "#123"),
    ("color", "#1234567"), ("color", "#gg0000"), ("color", "red"),
])
def test_invalid_input_blocks_entire_transaction_and_dynamic_preview_is_silent(dialog, field, value):
    dialog.preview_button.invoke()
    dialog._draft_vars["text"].set("Keep draft")
    before = dialog.app.app_settings.settings_file.read_bytes()
    dialog._draft_vars[field].set(value)
    assert dialog.app.errors == []
    dialog.apply_changes()
    assert len(dialog.app.errors) == 1
    assert dialog.window is not None
    assert dialog._draft()[field] == value
    assert dialog._draft()["text"] == "Keep draft"
    assert dialog.app.app_settings.settings_file.read_bytes() == before


@pytest.mark.parametrize("size", [1, 100])
def test_apply_validates_and_saves_once_closing_preview(dialog, monkeypatch, size):
    dialog.preview_button.invoke()
    variables = list(dialog._draft_vars.values())
    dialog._draft_vars["size"].set(str(size))
    dialog._draft_vars["color"].set("#abcdef")
    dialog._draft_vars["text"].set("Applied")
    saves = []
    save = dialog.app.app_settings.save_settings

    def save_once():
        saves.append(dialog.app.app_settings.snapshot())
        return save()

    monkeypatch.setattr(dialog.app.app_settings, "save_settings", save_once)
    dialog.apply_changes()
    assert len(saves) == 1
    assert saves[0]["watermark_color"] == "#ABCDEF"
    assert saves[0]["watermark_size"] == size
    assert saves[0]["watermark_text"] == "Applied"
    assert dialog.window is None
    assert dialog.app.preview_window_handler.window is None
    assert all(not variable.trace_info() for variable in variables)


def test_disabled_controls_and_direct_callbacks_cannot_edit_values(dialog, monkeypatch):
    dialog.enable_button.invoke()
    draft = dialog._draft()
    assert draft["enabled"] == 0
    assert all(str(control["state"]) == "disabled" for control in dialog._dependent_controls)
    assert str(dialog.apply_button["state"]) == "normal"
    assert str(dialog._cancel_button["state"]) == "normal"
    monkeypatch.setattr("src.gui.dialogs.colorchooser.askcolor", lambda **kwargs: pytest.fail("Opened disabled picker"))
    for control in dialog._dependent_controls:
        if isinstance(control, (tk.Button, tk.ttk.Button)) and control["command"]:
            dialog.parent.tk.call(control["command"])
    dialog.position_menu.invoke(0)
    dialog.window.focus_force()
    dialog.parent.update()
    dialog.window.event_generate("<Shift-Right>")
    dialog.nudge_buttons[0].event_generate("<ButtonPress-1>")
    assert dialog._draft() == draft
    assert not dialog.parent.tk.call("after", "info")
    dialog.set_busy(True)
    dialog.set_busy(False)
    dialog.refresh_theme()
    assert all(str(control["state"]) == "disabled" for control in dialog._dependent_controls)
    dialog.enable_button.invoke()
    assert all(str(control["state"]) == "normal" for control in dialog._dependent_controls)
    assert dialog._draft() == draft | {"enabled": 1}
    dialog.enable_button.invoke()
    dialog.apply_changes()
    dialog.open()
    assert dialog._draft() == draft
    assert all(str(control["state"]) == "disabled" for control in dialog._dependent_controls)


@pytest.mark.parametrize("stop_way", ["disable", "busy", "focus_out", "release"])
def test_position_repeat_stops_on_state_and_focus_changes(dialog, stop_way):
    button = dialog.nudge_buttons[3]
    button.event_generate("<ButtonPress-1>")
    assert dialog._draft()["x_ratio"] == 0.2525
    assert dialog.parent.tk.call("after", "info")
    if stop_way == "disable":
        dialog.enable_button.invoke()
    elif stop_way == "busy":
        dialog.set_busy(True)
    elif stop_way == "focus_out":
        dialog.window.event_generate("<FocusOut>")
    else:
        button.event_generate("<ButtonRelease-1>")
    assert not dialog.parent.tk.call("after", "info")


@pytest.mark.parametrize("field", ["text", "size"])
def test_arrow_keys_in_entries_do_not_move_watermark(dialog, field):
    entry = dialog.text_entry if field == "text" else dialog.size_entry
    entry.focus_force()
    dialog.parent.update()
    before = dialog._draft()
    entry.event_generate("<Right>")
    assert dialog._draft() == before
    assert not dialog.parent.tk.call("after", "info")


@pytest.mark.parametrize("nested_action", ["close", "disable", "busy", "rebuild"])
def test_late_native_color_picker_result_cannot_change_stale_or_disabled_draft(dialog, monkeypatch, nested_action):
    def pick(**kwargs):
        if nested_action == "close":
            dialog.close()
        elif nested_action == "disable":
            dialog.enable_button.invoke()
        elif nested_action == "busy":
            dialog.set_busy(True)
        else:
            dialog.refresh_ui_strings()
        return (0, 0, 0), "#FF0000"

    monkeypatch.setattr("src.gui.dialogs.colorchooser.askcolor", pick)
    custom_button = next(control for control in dialog._dependent_controls
                         if isinstance(control, tk.ttk.Button) and control["text"] == "Custom")
    custom_button.invoke()
    if dialog.window is not None:
        assert dialog._draft()["color"] == "#123456"
    assert dialog.app.app_settings.snapshot()["watermark_color"] == "#123456"


def test_settings_write_failure_keeps_validated_session_values_and_notice(dialog, monkeypatch):
    before = dialog.app.app_settings.settings_file.read_bytes()
    dialog._draft_vars["text"].set("Session value")
    monkeypatch.setattr("src.config.settings.os.replace", lambda *args: (_ for _ in ()).throw(PermissionError("denied")))
    dialog.apply_changes()
    assert dialog.window is None
    assert dialog.app.app_settings.snapshot()["watermark_text"] == "Session value"
    assert dialog.app.app_settings.settings_file.read_bytes() == before
    assert dialog.app.app_settings.take_notices()[0].kind == "write_failed"


@pytest.mark.parametrize("close_way", ["protocol", "destroy"])
def test_closing_preview_directly_clears_references_and_keeps_dialog_draft(dialog, close_way):
    dialog.preview_button.invoke()
    preview = dialog.app.preview_window_handler
    before = dialog._draft()
    if close_way == "protocol":
        dialog.parent.tk.call(preview.window.protocol("WM_DELETE_WINDOW"))
    else:
        preview.window.destroy()
    assert preview.window is None
    assert preview.last_origin is None
    assert preview.last_watermark_data == {}
    assert preview.page_photo is preview.watermark_photo is None
    assert dialog._draft() == before
    dialog.preview_button.invoke()
    assert preview.is_open()


def test_busy_language_and_theme_changes_keep_draft_locked_until_release(dialog):
    before = dialog._draft()
    dialog.app.output_options_locked = True
    dialog.set_busy(True)
    dialog.app.strings = build_strings(setup_translation("de")[0])
    dialog.refresh_ui_strings()
    PDFHighlighterApp._apply_theme_to_open_windows(dialog.app)
    assert dialog._draft() == before
    assert str(dialog.enable_button["state"]) == "disabled"
    assert str(dialog.apply_button["state"]) == "disabled"
    assert str(dialog._cancel_button["state"]) == "normal"
    assert all(str(control["state"]) == "disabled" for control in dialog._dependent_controls)
    dialog.apply_changes()
    assert dialog.window is not None
    dialog.app.output_options_locked = False
    dialog.set_busy(False)
    assert dialog._draft() == before
    assert all(str(control["state"]) == "normal" for control in dialog._dependent_controls)


def test_disabling_hides_existing_preview_even_with_invalid_draft_fields(dialog):
    dialog.preview_button.invoke()
    preview = dialog.app.preview_window_handler
    assert preview.watermark_item is not None
    dialog._draft_vars["size"].set("unfinished")
    dialog._draft_vars["color"].set("#invalid")
    dialog.enable_button.invoke()
    assert preview.canvas.itemcget(preview.watermark_item, "state") == "hidden"
    assert dialog._draft()["size"] == "unfinished"
    assert dialog._draft()["color"] == "#invalid"
    assert dialog.app.errors == []


def test_parent_destroyed_during_native_picker_does_not_raise_callback_error(dialog, monkeypatch):
    def pick(**kwargs):
        dialog.close()
        raise tk.TclError("parent destroyed")

    monkeypatch.setattr("src.gui.dialogs.colorchooser.askcolor", pick)
    custom_button = next(control for control in dialog._dependent_controls
                         if isinstance(control, tk.ttk.Button) and control["text"] == "Custom")
    custom_button.invoke()
    assert dialog.window is None
