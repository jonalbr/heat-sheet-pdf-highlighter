"""Committed option summaries and usable layouts with real Tk widgets."""

from dataclasses import replace
from pathlib import Path

import pytest

from test_settings_reset import app as app
from src.gui.main_window import ProcessingJob
from src.models import MarkingMode


@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize("mode", list(MarkingMode))
def test_summary_counts_only_active_names_and_translates(app, language, mode):
    app.names_var.set("Alice, Bob\n, Clara")
    app.marking_mode_var.set(mode.value)
    app.on_language_change(language)
    text = app.options_label.cget("text")
    count = "3 Namen" if language == "de" else "3 names"
    assert (count in text) is (mode != MarkingMode.ALL_MATCHES)
    assert text.startswith("Aktive Optionen:" if language == "de" else "Active options:")
    assert "Alice" not in text


@pytest.mark.parametrize("count", [0, 1, 2])
def test_summary_name_plural_and_relevance(app, count):
    app.on_language_change("en")
    app.marking_mode_var.set(MarkingMode.NAMED_MATCHES.value)
    app.names_var.set(",".join(["Alice"] * count))
    assert f"{count} " + ("name ·" if count == 1 else "names ·") in app._options_text()
    app.relevant_lines_var.set(0)
    assert "All lines" in app.options_label.cget("text")
    app.relevant_lines_var.set(1)
    assert "Relevant lines only" in app.options_label.cget("text")


@pytest.mark.parametrize(
    "enabled,text,expected",
    [
        ("False", "Saved", "Watermark off"),
        ("True", "Saved", "Watermark on"),
        ("True", " \t ", "Watermark has no text"),
    ],
)
def test_summary_watermark_and_ocr_settings(app, enabled, text, expected):
    app.on_language_change("en")
    app.app_settings.update_settings(
        dict(watermark_enabled=enabled, watermark_text=text, ocr_enabled="True", ocr_language="deu+eng")
    )
    app._render_options()
    assert expected in app.options_label.cget("text")
    assert app.options_label.cget("text").endswith("OCR on demand")
    app.app_settings.update_setting("ocr_enabled", "False")
    app._render_options()
    assert "OCR off" in app.options_label.cget("text")


@pytest.mark.parametrize("phase", ["preparing", "processing", "cancelling", "saving", "pending"])
def test_job_summary_uses_snapshot_until_resources_are_released(app, phase):
    app.on_language_change("en")
    app.names_var.set("Alice")
    app.marking_mode_var.set(MarkingMode.NAMED_MATCHES.value)
    job = ProcessingJob(1, "source.pdf", app._collect_processing_options(), state=phase)
    app.names_var.set("Changed, Later")
    app.marking_mode_var.set(MarkingMode.ALL_MATCHES.value)
    if phase == "pending":
        app._pending_save = job
    else:
        app._job = job
    app._render_operation()
    assert "Job options: Only listed names · 1 name" in app.options_label.cget("text")
    app.on_language_change("de")
    assert "Auftragsoptionen: Nur gelistete Namen · 1 Name" in app.options_label.cget("text")
    app._job = app._pending_save = None
    app._render_operation()
    assert "Aktive Optionen: Alle Treffer" in app.options_label.cget("text")


@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize("ocr_language", ["deu", "eng", "deu+eng"])
def test_ocr_summary_is_on_demand_regardless_of_prompt_language(app, language, ocr_language):
    app.on_language_change(language)
    job = ProcessingJob(1, "source.pdf", replace(app._collect_processing_options(), ocr_language=ocr_language))
    app._job = job
    app.app_settings.update_setting("ocr_enabled", "False")
    app._render_options()
    assert app.options_label.cget("text").endswith("OCR bei Bedarf" if language == "de" else "OCR on demand")
    assert job.options.ocr_language == ocr_language
    app._job = None


def test_dialog_drafts_apply_cancel_and_reset_refresh_summary(app):
    app.on_language_change("en")
    before = app.options_label.cget("text")
    app.filter_dialog.open()
    app.filter_dialog.mode.set(MarkingMode.NAMED_MATCHES.value)
    app.filter_dialog.names_entry.insert("end", "Alice")
    assert app.options_label.cget("text") == before
    app.filter_dialog.close()
    assert app.options_label.cget("text") == before
    app.watermark_dialog.open()
    app.watermark_dialog._draft_vars["enabled"].set(1)
    app.watermark_dialog._draft_vars["text"].set("Draft")
    assert app.options_label.cget("text") == before
    app.watermark_dialog.apply_changes()
    assert "Watermark on" in app.options_label.cget("text")
    app.filter_dialog.open()
    app.filter_dialog.mode.set(MarkingMode.NAMED_MATCHES.value)
    app.filter_dialog.names_entry.delete("1.0", "end")
    app.filter_dialog.names_entry.insert("end", "Alice")
    app.filter_dialog.apply_changes()
    assert "Only listed names · 1 name" in app.options_label.cget("text")
    app.reset_settings()
    app.on_language_change("en")
    assert "All matches" in app.options_label.cget("text")
    assert "Watermark off" in app.options_label.cget("text")


@pytest.mark.parametrize("language,theme", [("de", "light"), ("en", "dark"), ("de", "system")])
def test_small_layout_wraps_and_scrolls_with_large_font_and_long_result(app, language, theme):
    app.ui_font.configure(size=18)
    app.heading_font.configure(size=24)
    app.on_language_change(language)
    app._on_theme_mode_change(theme)
    assert app.ui_font.cget("size") == 18
    assert str(app.result_label.cget("font")) == str(app.ui_font)
    assert str(app.entry_file.cget("font")) == str(app.ui_font)
    assert app.button_filter.winfo_reqwidth() >= app.ui_font.measure(app.button_filter.cget("text"))
    target = "C:/" + "very long folder name/" * 20 + "saved result.pdf"
    job = ProcessingJob(
        1, "source.pdf", app._collect_processing_options(), started=True, result=("saved", 3, 0, None, 1, 3, target)
    )
    app._result_job = job
    app._render_result()
    app.root.deiconify()
    app.root.geometry(f"{app.root.minsize()[0]}x360+0+0")
    app.root.update()
    assert app.button_filter.winfo_width() >= app.button_filter.winfo_reqwidth()
    assert app.button_watermark.winfo_width() >= app.button_watermark.winfo_reqwidth()
    assert target in app.result_label.cget("text")
    assert app.content.winfo_height() > app.content_canvas.winfo_height()
    assert app.result_label.winfo_width() < app.root.winfo_width()
    button = app.result_buttons["result_open_folder"]
    button.focus_force()
    app.root.update()
    assert app.content_canvas.yview()[0] > 0
    assert app.checkbox_relevant_lines.grid_info()["row"] != app.options_actions.grid_info()["row"]
    # User enlargement survives translation; no fixed-size reset.
    app.root.geometry("1000x700")
    app.root.update()
    app.on_language_change("en")
    assert app.root.winfo_width() >= 1000


def test_minimum_is_bounded_by_available_screen(app, monkeypatch):
    app.ui_font.configure(size=18)
    monkeypatch.setattr(app.root, "winfo_screenwidth", lambda: 640)
    monkeypatch.setattr(app.root, "winfo_screenheight", lambda: 480)
    app._update_layout_metrics()
    width, height = app.root.minsize()
    assert width <= 600 and height <= 400


def test_full_source_path_tooltip_survives_translation(app, monkeypatch, tmp_path):
    source = str(tmp_path / ("long folder name " * 5) / ("long event name " * 8 + ".pdf"))
    app.app_settings.update_setting("ocr_enabled", "False")
    app.marking_preview.prepare = lambda _path: None
    monkeypatch.setattr("src.gui.main_window.filedialog.askopenfilename", lambda **kwargs: source)
    app.browse_file()
    assert app.entry_file._hsph_tooltip.text == source
    assert app.pdf_file_var.get() == Path(source).name
    app.on_language_change("en")
    assert app.entry_file._hsph_tooltip.text == source


@pytest.mark.parametrize("app", [b'{"language":"de"}', b'{"language":"en"}'], indirect=True)
def test_startup_height_fits_content_without_unused_space_below_result(app):
    mapped_sizes = []
    app.root.bind("<Map>", lambda event: mapped_sizes.append((app.root.winfo_width(), app.root.winfo_height()))
                  if event.widget is app.root else None, add="+")
    expected_height = (
        app.header_frame.winfo_reqheight() + app.content.winfo_reqheight()
        + app.version_frame.winfo_reqheight() + 4
    )
    app.root.deiconify()
    app.root.update()
    assert mapped_sizes == [(760, expected_height)]
    assert app.root.winfo_height() == expected_height
    assert abs(app.content_canvas.winfo_height() - app.content.winfo_reqheight()) <= 4
    assert app.content_canvas.yview() == (0.0, 1.0)
    gap = app.version_frame.winfo_rooty() - (app.result_frame.winfo_rooty() + app.result_frame.winfo_height())
    assert 0 <= gap <= 12


@pytest.mark.parametrize("kind", ["filter", "watermark"])
@pytest.mark.parametrize("language,theme", [("de", "light"), ("en", "dark")])
def test_dialog_actions_stay_grouped_at_right_edge(app, kind, language, theme):
    app.on_language_change(language)
    app._on_theme_mode_change(theme)
    dialog = app.filter_dialog if kind == "filter" else app.watermark_dialog
    dialog.open()
    app.root.update()
    apply = dialog.apply_button
    cancel = dialog.cancel_button if kind == "filter" else dialog._cancel_button
    assert apply.master is cancel.master
    for extra_width in (0, 160):
        window = dialog.window
        window.geometry(f"{window.winfo_width() + extra_width}x{window.winfo_height()}")
        app.root.update()
        assert apply.winfo_rooty() == cancel.winfo_rooty()
        assert cancel.winfo_rootx() - (apply.winfo_rootx() + apply.winfo_width()) == 8
        right_margin = window.winfo_rootx() + window.winfo_width() - (cancel.winfo_rootx() + cancel.winfo_width())
        assert 8 <= right_margin <= 16


def test_main_scrollbar_empty_action_is_a_noop_and_valid_actions_still_scroll(app, monkeypatch):
    errors = []
    monkeypatch.setattr(app.root, "report_callback_exception", lambda *args: errors.append(args))
    app.root.geometry("760x300")
    app.root.deiconify()
    app.root.update()
    command = app.content_scrollbar.cget("command")
    before = app.content_canvas.yview()
    app.root.tk.call(command, "")  # Reproduce the reported Tk callback arguments.
    assert app.content_canvas.yview() == before
    app.root.tk.call(command, "moveto", "0.5")
    moved = app.content_canvas.yview()[0]
    assert moved > 0
    app.root.tk.call(command, "scroll", "-1", "units")
    assert app.content_canvas.yview()[0] < moved
    assert errors == []
