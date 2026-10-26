"""Persistent completion widgets with real Tk and translated result snapshots."""

from types import SimpleNamespace

import pytest

from test_settings_reset import app as app
from src.gui.main_window import ProcessingJob


def set_result(app, result):
    job = ProcessingJob(1, "source.pdf", app._collect_processing_options(), started=True)
    job.result = result
    app._result_job = job
    app._render_result()
    app.root.update_idletasks()
    return job


@pytest.mark.parametrize("language", ["de", "en"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_result_survives_language_theme_and_finalization(app, language, theme, tmp_path):
    target = str(tmp_path / "Folder with spaces" / "Saved result.pdf")
    warning = SimpleNamespace(reduction_failed=True, output_size=20 * 1024 * 1024)
    job = set_result(app, ("saved", 4, 0, warning, 2, 2, target))
    job.committed = True
    app._job = job
    app.finalize_processing(job)
    app._on_theme_mode_change(theme)
    app.on_language_change(language)
    assert app._result_job is job
    assert app._status[0] == "status_done"
    text = app.result_label.cget("text")
    assert target in text and "20.0" in text
    assert ("2 Zeilen neu markiert." if language == "de" else "2 lines newly highlighted.") in text
    assert str(app.result_buttons["result_open_pdf"]["state"]) == "normal"
    assert str(app.result_buttons["result_open_folder"]["state"]) == "normal"
    assert not app.result_buttons["result_retry"].winfo_manager()
    assert app.notices == []  # No modal success dialog.
    assert app.root.resizable() == (1, 1)


def test_pending_result_controls_and_language_switch_keep_retry_resources(app):
    job = set_result(app, ("save_failed", "locked output"))
    app._pending_save = job
    app._render_operation()
    assert str(app.start_abort_button["state"]) == "disabled"
    assert not app.result_buttons["result_open_pdf"].winfo_manager()
    assert str(app.result_buttons["result_retry"]["state"]) == "normal"
    app.on_language_change("de")
    assert "Ergebnis bleibt erhalten" in app.result_label.cget("text")
    assert app._pending_save is job
    app.discard_pending_save()
    assert "abgebrochen" in app.result_label.cget("text")
    assert str(app.result_buttons["result_open_pdf"]["state"]) == "disabled"


@pytest.mark.parametrize("result", [("aborted",), ("failed", "broken PDF"), ("info_key", "val_nothing")])
def test_unsaved_terminal_results_remain_distinct_and_disable_open_buttons(app, result, monkeypatch):
    monkeypatch.setattr("src.gui.main_window.show_error", lambda *args: None)
    job = set_result(app, result)
    app._job = job
    app.finalize_processing(job)
    assert app._result_job is job
    assert app._status[0] == {"aborted": "status_aborted_processing", "failed": "result_failed", "info_key": "val_nothing"}[result[0]]
    for key in ("result_open_pdf", "result_open_folder"):
        assert str(app.result_buttons[key]["state"]) == "disabled"


def test_target_change_failure_detail_is_retranslated(app):
    job = set_result(app, ("save_failed", "result_target_changed"))
    app._pending_save = job
    app.on_language_change("en")
    assert "destination has changed" in app.result_label.cget("text")
    app.on_language_change("de")
    assert "Zieldatei wurde seit der Auswahl verändert" in app.result_label.cget("text")
