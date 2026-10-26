"""Keep technical failure diagnostics in logs rather than user messages."""

import queue
from types import SimpleNamespace

import pytest

from src.gui.dialogs import UpdateDialogs
from src.gui.preview import PreviewWindow
from src.gui.ui_strings import build_strings
from src.utils.localization import setup_translation


DETAIL = "RuntimeError: worker exited with code 7; C:/Temp/hsph-secret.pdf; TLS traceback"


@pytest.fixture(params=["de", "en"])
def app(request):
    return SimpleNamespace(
        strings=build_strings(setup_translation(request.param)[0]),
        root=object(),
        input_file_full_path="source.pdf",
        _ui_messages=queue.Queue(),
    )


def test_preview_failure_only_shows_actionable_message(app, monkeypatch, caplog):
    preview = PreviewWindow(app)
    errors = []

    def fail(_page):
        raise RuntimeError(DETAIL)

    monkeypatch.setattr(preview, "_get_page_image", fail)
    monkeypatch.setattr("src.gui.preview.show_error", lambda app, title, text: errors.append(text))
    preview.preview_watermark(1, "TXT", "#FF0000", 16, "top", 0.5, 0.05, 1)
    assert errors == [app.strings["preview_failed"]]
    assert DETAIL not in errors[0]
    assert DETAIL in caplog.text


def test_update_check_failure_keeps_retry_and_logs_details(app, monkeypatch, caplog):
    messages = []
    monkeypatch.setattr("src.gui.dialogs.ask_retry_cancel", lambda app, title, text: messages.append(text) or True)
    assert UpdateDialogs(app).show_update_error_retry(DETAIL) is True
    assert messages == [app.strings["upd_check_failed"]]
    assert DETAIL not in messages[0]
    assert DETAIL in caplog.text


@pytest.mark.parametrize("active", [False, True])
def test_update_failure_is_scheduled_without_showing_diagnostics(app, monkeypatch, caplog, active):
    app.download_active = active
    messages = []
    monkeypatch.setattr("src.gui.dialogs.show_error", lambda app, title, text: messages.append(text))
    UpdateDialogs(app).show_download_error(DETAIL)
    assert messages == []  # Preserve main-thread dispatch for both paths.
    _, kind, args = app._ui_messages.get_nowait()
    callback = args[0] if kind == "download" else args[0].callback
    callback()
    assert messages == [app.strings["upd_download_failed"]]
    assert DETAIL not in messages[0]
    assert DETAIL in caplog.text
