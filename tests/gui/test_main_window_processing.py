"""Deterministic job races with queued Tk callbacks and controlled workers."""

from pathlib import Path
import queue
import threading
from types import SimpleNamespace

import pytest
from pymupdf import Document as PdfDocument

from src.gui import main_window
from src.gui.main_window import GuiRequest, PDFHighlighterApp, ProcessingJob
from src.gui.ui_strings import build_strings, plural_strings
from src.utils.localization import setup_translation


class Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        assert threading.current_thread() is threading.main_thread(), "Worker read Tk variable"
        return self.value

    def set(self, value):
        self.value = value


class Widget(dict):
    def configure(self, **kwargs):
        self.update(kwargs)

    config = configure

    def stop(self):
        pass

    def start(self, *args):
        pass


class Root:
    def __init__(self):
        self.callbacks = []
        self.destroyed = False

    def after(self, delay, callback, *args):
        self.callbacks.append(lambda: callback(*args))

    def after_idle(self, callback):
        self.callbacks.append(callback)

    def winfo_children(self):
        return []

    def destroy(self):
        self.destroyed = True


@pytest.fixture
def app(monkeypatch, tmp_path):
    app = PDFHighlighterApp.__new__(PDFHighlighterApp)
    app.root = Root()
    app.paths = SimpleNamespace(is_valid_path=lambda path: None, ocr_tessdata_dir="tessdata")
    settings = dict(ocr_enabled="False", search_str="CLUB", watermark_enabled="True", watermark_text="Original",
                    watermark_color="#FF0000", watermark_size=16, watermark_position="top",
                    watermark_x_ratio=0.2, watermark_y_ratio=0.3, ocr_dpi=300, ocr_language="deu",
                    ocr_reduce_large_outputs="True")
    app.app_settings = SimpleNamespace(
        settings=settings, update_setting=lambda key, value: settings.update({key: value}),
        snapshot=lambda: dict(settings), take_notices=lambda: [],
    )
    for name, value in dict(search_phrase="CLUB", relevant_lines=1, marking_mode="all_matches", names="Alice, Bob",
                            highlight_color_default="#FFFFA6", highlight_color_names="#C4FAF8").items():
        setattr(app, f"{name}_var", Var(value))
    app.status_var = Var()
    for name in ("browse_button", "entry_search_str", "entry_file", "checkbox_relevant_lines", "button_filter",
                 "button_watermark", "start_abort_button", "progress_bar"):
        setattr(app, name, Widget())
    for name in ("filter_dialog", "watermark_dialog", "dev_tools"):
        setattr(app, name, SimpleNamespace(set_busy=lambda busy: None, close=lambda: None, window=None))
    app.preview_window_handler = SimpleNamespace(close=lambda: None)
    app._job = None
    app._next_job_id = 0
    app._ui_messages = queue.Queue()
    app._closing = False
    app._download_id = 0
    app._download_cancel = threading.Event()
    app.processing_active = app.download_active = False
    app._status = ("status_waiting", ())
    app.ocr_detection_path = app.ocr_detection_result = None
    app.input_file_full_path = "input.pdf"
    app.update_checker = SimpleNamespace(_active_download=False)
    app.strings = build_strings(lambda text: text)
    app.plural_strings = plural_strings
    app.n_ = lambda singular, plural, count: singular if count == 1 else plural
    app.output = str(tmp_path / "out.pdf")
    app._ask_output_file = lambda path: app.output
    app.infos = []
    app.errors = []
    monkeypatch.setattr(main_window, "show_info", lambda app, title, message: app.infos.append((title, message)))
    monkeypatch.setattr(main_window, "show_error", lambda app, title, message: app.errors.append((title, message)))
    monkeypatch.setattr("src.gui.dialogs.show_error", lambda app, title, message: app.errors.append((title, message)))
    app.documents = []

    class Document:
        is_encrypted = False

        def __init__(self, path):
            self.path = path
            self.closed = False
            app.documents.append(self)

        def __len__(self):
            return 2

        def __getitem__(self, index):
            return index

        def save(self, path):
            pass

        def close(self):
            self.closed = True

    monkeypatch.setattr(main_window, "Document", Document)
    monkeypatch.setattr(main_window, "highlight_matching_data", lambda **kwargs: (1, 0, 1))
    monkeypatch.setattr(main_window, "watermark_pdf_page", lambda *args: None)
    monkeypatch.setattr(main_window, "save_compact_pdf", lambda *args: None)
    yield app
    if app._job and app._job.worker:
        app._job.cancel.set()
        app._job.worker.join(timeout=2)
        assert not app._job.worker.is_alive()
    app._job = None
    app.discard_pending_save()


def serve_until(app, event):
    while not event.is_set():
        message = app._ui_messages.get(timeout=2)
        app._ui_messages.put(message)
        app._poll_work()
        if message[1] == "started":
            assert event.wait(2)


def finish(app, job):
    """Wait for messages, not elapsed time, while serving main-thread requests."""
    while job.result is None:
        message = app._ui_messages.get(timeout=2)
        app._ui_messages.put(message)
        app._poll_work()
    job.worker.join(timeout=2)
    assert not job.worker.is_alive()
    app._poll_work()


@pytest.mark.parametrize("matches,skipped,applied,export", [
    (0, 0, (False, False), False), (2, 2, (False, False), False),
    (1, 0, (False, False), True), (0, 0, (True, True), True),
    (2, 2, (False, True), True), (2, 1, (True, True), True),
])
def test_export_decision_uses_applied_changes_and_counts_watermarked_pages(app, monkeypatch, matches, skipped, applied, export):
    targets, saves = [], []
    monkeypatch.setattr(main_window, "highlight_matching_data", lambda **kwargs: (matches, skipped, int(matches > skipped)))
    monkeypatch.setattr(main_window, "watermark_pdf_page", lambda page, settings: applied[page])
    monkeypatch.setattr(app, "_ask_output_file", lambda path: targets.append(path) or app.output)
    monkeypatch.setattr(main_window, "save_compact_pdf", lambda document, path: saves.append(path))
    app.start_processing()
    finish(app, app._job)
    assert bool(targets)
    assert bool(saves) is export
    if export:
        assert app.last_processing_result == ("saved", matches * 2, skipped * 2, None, sum(applied), 2 * int(matches > skipped), app.output)
        assert ("Watermark applied" in app._result_text()) is any(applied)
    else:
        assert app.last_processing_result == ("info_key", "val_nothing")
    assert all(document.closed for document in app.documents)


@pytest.mark.parametrize("enabled,text,expected_pages", [
    ("True", "Watermark only", 2), ("True", "", 0), ("True", " \t\n ", 0),
    ("False", "Disabled watermark", 0),
])
def test_watermark_only_export_with_real_pdf(app, monkeypatch, tmp_path, enabled, text, expected_pages):
    from src.core.watermark import watermark_pdf_page
    from src.core.pdf_processor import highlight_matching_data

    source, output = tmp_path / "source.pdf", tmp_path / "output.pdf"
    with PdfDocument() as document:
        document.new_page(width=400, height=300)
        document.new_page(width=400, height=300)
        document.save(source)
    app.input_file_full_path = str(source)
    app.app_settings.settings.update(watermark_enabled=enabled, watermark_text=text)
    asked = []
    monkeypatch.setattr(main_window, "Document", PdfDocument)
    monkeypatch.setattr(main_window, "highlight_matching_data", highlight_matching_data)
    monkeypatch.setattr(main_window, "watermark_pdf_page", watermark_pdf_page)
    monkeypatch.setattr(main_window, "save_compact_pdf", lambda document, path: document.save(path))
    monkeypatch.setattr(app, "_ask_output_file", lambda path: asked.append(path) or str(output))
    app.start_processing()
    finish(app, app._job)
    assert output.exists() is bool(expected_pages)
    assert bool(asked)
    if expected_pages:
        assert app.last_processing_result == ("saved", 0, 0, None, expected_pages, 0, str(output))
        with PdfDocument(output) as document:
            assert all(text in page.get_text() for page in document)
    else:
        assert app.last_processing_result == ("info_key", "val_nothing")


@pytest.mark.parametrize("language,count", [("en", 1), ("en", 2), ("de", 1), ("de", 2)])
def test_watermark_page_completion_uses_translated_plural(app, monkeypatch, language, count):
    gettext, app.n_ = setup_translation(language)
    app.strings = build_strings(gettext)
    monkeypatch.setattr(main_window, "highlight_matching_data", lambda **kwargs: (0, 0, 0))
    monkeypatch.setattr(main_window, "watermark_pdf_page", lambda page, settings: page < count)
    app.start_processing()
    finish(app, app._job)
    expected = (f"Watermark applied to {count} " + ("page." if count == 1 else "pages.")) if language == "en" else (
        f"Wasserzeichen auf {count} " + ("Seite angewendet." if count == 1 else "Seiten angewendet."))
    assert expected in app._result_text()


def test_cancel_after_watermark_does_not_offer_export(app, monkeypatch):
    monkeypatch.setattr(main_window, "highlight_matching_data", lambda **kwargs: (0, 0, 0))

    def watermark(page, settings):
        app._job.cancel.set()
        return True

    monkeypatch.setattr(main_window, "watermark_pdf_page", watermark)

    app.start_processing()
    finish(app, app._job)
    assert app.last_processing_result == ("aborted",)
    assert app.infos == []


def test_watermark_insertion_failure_is_a_processing_failure(app, monkeypatch):
    def fail(*args):
        raise RuntimeError("watermark insertion failed")

    monkeypatch.setattr(main_window, "watermark_pdf_page", fail)

    app.start_processing()
    finish(app, app._job)
    assert app.last_processing_result == ("failed", "watermark insertion failed")
    assert app.infos == []
    assert app.errors


def test_snapshot_is_used_between_pages_and_worker_never_reads_tk(app, monkeypatch):
    highlights, watermarks, saved = [], [], []
    first_page, next_page = threading.Event(), threading.Event()

    def highlight(**kwargs):
        highlights.append(kwargs)
        if len(highlights) == 1:
            first_page.set()
            assert next_page.wait(2)
        return 1, 0, 1

    monkeypatch.setattr(main_window, "highlight_matching_data", highlight)
    monkeypatch.setattr(main_window, "watermark_pdf_page", lambda page, settings: watermarks.append(dict(settings)))
    monkeypatch.setattr(main_window, "save_compact_pdf", lambda doc, path: saved.append(path))
    app.start_processing()
    job = app._job
    try:
        serve_until(app, first_page)
        for name, value in dict(search_phrase="NEW", relevant_lines=0, marking_mode="named_matches", names="Changed",
                                highlight_color_default="#000000", highlight_color_names="#111111").items():
            getattr(app, f"{name}_var").set(value)
        app.app_settings.settings.update(watermark_enabled="False", watermark_text="Changed", watermark_color="#000000",
                                         watermark_size=40, watermark_position="bottom", watermark_x_ratio=0.8,
                                         watermark_y_ratio=0.9, ocr_dpi=72, ocr_reduce_large_outputs="False")
    finally:
        next_page.set()
    finish(app, job)
    assert all(call["only_relevant"] and call["names"] == ("Alice", "Bob") for call in highlights)
    assert all(call["highlight_color_default"] == "#FFFFA6" for call in highlights)
    assert [call["search_str"] for call in highlights] == ["CLUB", "CLUB"]
    assert [item["watermark_text"] for item in watermarks] == ["Original", "Original"]
    assert len(saved) == 1
    assert Path(saved[0]).parent == Path(app.output).parent
    assert saved[0] != app.output
    assert app.last_processing_result[0] == "saved"
    assert all(doc.closed for doc in app.documents)
    assert app.infos == []
    assert "Finished" in app._result_text()


def test_cancel_cannot_restart_until_worker_has_exited_even_after_result(app, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    spawned = []

    def worker(job):
        spawned.append(job)
        app._post_job(job, "result", "aborted")
        entered.set()
        assert release.wait(2)

    monkeypatch.setattr(app, "process_pdf", worker)
    app.start_processing()
    job = app._job
    try:
        serve_until(app, entered)
        app.request_cancel_processing()
        app.request_cancel_processing()
        app.start_processing()
        app._poll_work()
        assert len(spawned) == 1
        assert app._job is job
        assert app.processing_active
        assert app.start_abort_button["state"] == "disabled"
        assert "Cancelling" in app.status_var.get()
        app.finalize_processing(job)
        assert app._job is job
    finally:
        release.set()
        job.worker.join(2)
    app._poll_work()
    assert app._job is None
    app.start_processing()
    second = app._job
    second.worker.join(2)
    assert second.identifier != job.identifier
    app._poll_work()
    assert len(spawned) == 2


def test_old_callbacks_and_dialog_requests_cannot_affect_new_job(app):
    old = ProcessingJob(1, "old.pdf", app._collect_processing_options())
    new = ProcessingJob(2, "new.pdf", old.options)
    app._job = new
    app.processing_active = True
    app._set_status("status_preparing")
    called = []
    request = GuiRequest(lambda: called.append(True))
    for kind, args in (("pages", (2, 2, 9, 0)), ("ocr", (9, 9)),
                       ("phase", ("saving", "status_saving")), ("request", (request,)),
                       ("result", ("failed", "old error"))):
        app._post_job(old, kind, *args)
    app.finalize_processing(old)
    app._poll_work()
    assert app._job is new
    assert new.result is None
    assert request.ready.is_set()
    assert called == app.errors == app.infos == []
    assert app.status_var.get() == app.strings["status_preparing"]


def test_cancel_during_pages_skips_remaining_pages_and_save(app, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def highlight(**kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(2)
        return 1, 0, 1

    monkeypatch.setattr(main_window, "highlight_matching_data", highlight)
    app.start_processing()
    job = app._job
    try:
        serve_until(app, entered)
        app.request_cancel_processing()
    finally:
        release.set()
    finish(app, job)
    assert len(calls) == 1
    assert app.last_processing_result == ("aborted",)
    assert app.infos == []
    assert app.documents[0].closed


def test_cancel_during_native_save_keeps_ownership_until_return(app, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def save(*args):
        entered.set()
        assert release.wait(2)

    monkeypatch.setattr(main_window, "save_compact_pdf", save)
    app.start_processing()
    job = app._job
    # Serve the output request; the worker now enters native saving.
    while not entered.is_set():
        message = app._ui_messages.get(timeout=2)
        app._ui_messages.put(message)
        app._poll_work()
        if message[1] == "request":
            assert entered.wait(2)
    try:
        app.request_cancel_processing()
        app.start_processing()
        app._poll_work()
        assert app._job is job
    finally:
        release.set()
    finish(app, job)
    assert app.last_processing_result == ("aborted",)
    assert app.infos == []


def test_ocr_confirmation_and_save_use_captured_options(app, monkeypatch):
    app.app_settings.settings["ocr_enabled"] = "True"
    app.ocr_detection_path = "input.pdf"
    app.ocr_detection_result = True
    paths, ocr_calls, save_calls = [], [], []
    app._confirm_ocr_processing = lambda language: "eng"
    monkeypatch.setattr(main_window, "ensure_bundled_tessdata", lambda *args: "tessdata")
    original_temp = app._create_temp_pdf_path

    def temp():
        path = original_temp()
        paths.append(path)
        return path

    app._create_temp_pdf_path = temp

    def ocr(*args, **kwargs):
        ocr_calls.append(kwargs)
        app.app_settings.settings.update(ocr_dpi=72, ocr_reduce_large_outputs="False", ocr_language="deu+eng")
        kwargs["progress_callback"](2, 2)

    monkeypatch.setattr(main_window, "create_searchable_ocr_pdf_in_process", ocr)
    saved_result = SimpleNamespace(reduction_failed=True, output_size=20 * 1024 * 1024)
    monkeypatch.setattr(main_window, "watermark_pdf_page", lambda *args: True)
    monkeypatch.setattr(main_window, "save_pdf_path_in_process",
                        lambda *args, **kwargs: save_calls.append(kwargs) or saved_result)
    app.start_processing()
    job = app._job
    finish(app, job)
    assert ocr_calls[0]["dpi"] == 300
    assert ocr_calls[0]["language"] == "eng"
    assert save_calls[0]["reduce_large_outputs"] is True
    assert save_calls[0]["is_cancelled"].__self__ is job.cancel
    assert app.last_processing_result[3] is saved_result
    assert app.last_processing_result[4] == 2
    assert "Watermark applied to 2 pages." in app._result_text()
    assert "20.0" in app._result_text()
    from pathlib import Path
    assert all(not Path(path).exists() for path in paths)
    assert all(doc.closed for doc in app.documents)


@pytest.mark.parametrize("stage", ["ocr", "save"])
def test_cancel_ocr_or_save_waits_for_process_cleanup(app, monkeypatch, stage):
    app.app_settings.settings["ocr_enabled"] = "True"
    app.ocr_detection_path = "input.pdf"
    app.ocr_detection_result = True
    app._confirm_ocr_processing = lambda language: "eng"
    monkeypatch.setattr(main_window, "ensure_bundled_tessdata", lambda *args: "tessdata")
    entered, cleanup = threading.Event(), threading.Event()

    def blocking(*args, **kwargs):
        entered.set()
        app._post_job(app._job, "barrier")
        assert cleanup.wait(2)
        assert kwargs["is_cancelled"]()
        raise main_window.OcrCancelled()

    monkeypatch.setattr(main_window, "create_searchable_ocr_pdf_in_process", blocking if stage == "ocr" else lambda *args, **kwargs: None)
    monkeypatch.setattr(main_window, "save_pdf_path_in_process", blocking)
    app.start_processing()
    job = app._job
    try:
        while not entered.is_set():
            message = app._ui_messages.get(timeout=2)
            app._ui_messages.put(message)
            app._poll_work()
        app.request_cancel_processing()
        app._poll_work()
        assert app._job is job
    finally:
        cleanup.set()
    finish(app, job)
    assert app.last_processing_result == ("aborted",)
    assert app.infos == []


def test_cancel_releases_unserved_gui_request(app, monkeypatch):
    requested = threading.Event()

    def worker(job):
        try:
            requested.set()
            app._request_job_ui(job, lambda: pytest.fail("Cancelled request was shown"))
        except main_window.OcrCancelled:
            app._post_job(job, "result", "aborted")

    monkeypatch.setattr(app, "process_pdf", worker)
    app.start_processing()
    job = app._job
    assert requested.wait(2)
    app.request_cancel_processing()
    job.worker.join(2)
    assert not job.worker.is_alive()
    app._poll_work()
    assert app._job is None


def test_validation_and_download_guard_prevent_start(app, monkeypatch):
    app.marking_mode_var.set("named_matches")
    app.names_var.set(" , ")
    monkeypatch.setattr(app, "process_pdf", lambda job: pytest.fail("Started invalid job"))
    app.start_processing()
    assert app.errors and app._job is None
    app.names_var.set("Alice")
    assert app.start_download()
    app.start_processing()
    assert app._job is None
    app.abort_download()
    app.start_processing()
    assert app.download_active
    app.finish_download()
    assert not app.output_options_locked


def test_processing_rejects_download_and_language_render_preserves_cancellation(app):
    job = ProcessingJob(1, "input.pdf", app._collect_processing_options())
    app._job = job
    assert app.start_download() is False
    app.request_cancel_processing()
    app.strings = build_strings(lambda text: "DE " + text)
    app._render_operation()
    assert app.status_var.get() == "DE Status: Cancelling …"
    assert app.start_abort_button["text"] == "DE Cancelling …"
    assert app.start_abort_button["command"] == app.request_cancel_processing
    assert app.start_abort_button["state"] == "disabled"


def test_worker_error_closes_document_and_reports_after_exit(app, monkeypatch, caplog):
    monkeypatch.setattr(main_window, "highlight_matching_data", lambda **kwargs: (_ for _ in ()).throw(ValueError("broken")))
    app.start_processing()
    job = app._job
    finish(app, job)
    assert app.last_processing_result == ("failed", "broken")
    assert app.errors[0][1] == app.strings["result_processing_failed"]
    assert "broken" in caplog.text
    assert app.documents[0].closed



@pytest.mark.parametrize("failure", ["http", "sha", "cancel"])
def test_download_reservation_released_on_all_terminal_paths(app, monkeypatch, failure):
    import requests
    from src.gui.dialogs import UpdateDialogs
    from src.utils.updater import UpdateChecker

    app.on_version_update = lambda *args: None
    app.update_dialogs = UpdateDialogs(app)
    checker = app.update_checker = UpdateChecker(app)
    paths = []

    def download(url, path):
        paths.append(path)
        app.start_processing()
        assert app._job is None
        if failure == "http":
            raise requests.HTTPError("offline")
        if failure == "cancel":
            app.abort_download()
        return failure == "cancel", 0

    monkeypatch.setattr(checker, "_download_with_progress", download)
    monkeypatch.setattr(checker, "_verify_sha256", lambda *args: False)
    monkeypatch.setattr(checker, "_spawn_installer", lambda *args: pytest.fail("Installer started"))
    checker.download_and_run_installer("exe", "sha")
    assert not checker._active_download
    assert app.download_active  # Only Tk may release the reservation.
    app._poll_work()
    assert not app.output_options_locked
    from pathlib import Path
    assert paths and all(not Path(path).exists() for path in paths)


def test_download_is_denied_before_network_when_pdf_owns_controls(app, monkeypatch):
    from src.gui.dialogs import UpdateDialogs
    from src.utils.updater import UpdateChecker

    app.on_version_update = lambda *args: None
    app.update_dialogs = UpdateDialogs(app)
    checker = app.update_checker = UpdateChecker(app)
    app._job = ProcessingJob(1, "input.pdf", app._collect_processing_options())
    monkeypatch.setattr(checker, "_download_with_progress", lambda *args: pytest.fail("Network before reservation"))
    checker.download_and_run_installer("exe")
    assert app._job is not None
    assert not checker._active_download
    assert not app.download_active


def test_concurrent_download_requests_and_old_callbacks(app, monkeypatch):
    from src.gui.dialogs import UpdateDialogs
    from src.utils.updater import UpdateChecker

    app.on_version_update = lambda *args: None
    app.update_dialogs = UpdateDialogs(app)
    checker = app.update_checker = UpdateChecker(app)
    entered, cleanup = threading.Event(), threading.Event()

    def download(*args):
        entered.set()
        assert cleanup.wait(2)
        return True, 0

    monkeypatch.setattr(checker, "_download_with_progress", download)
    worker = threading.Thread(target=lambda: checker.download_and_run_installer("exe"))
    worker.start()
    try:
        message = app._ui_messages.get(timeout=2)
        assert message[1] == "download_request"
        app._ui_messages.put(message)
        second = threading.Thread(target=lambda: checker.download_and_run_installer("exe"))
        second.start()
        second.join(2)
        assert not second.is_alive()
        app._poll_work()
        assert entered.wait(2)
        old_id = app._download_id
        app.abort_download()
        app.start_processing()
        assert app._job is None
        assert app.start_abort_button["command"] == app.abort_download
        assert app.start_abort_button["state"] == "disabled"
    finally:
        cleanup.set()
        worker.join(2)
    assert not worker.is_alive()
    app._poll_work()
    assert not app.download_active
    app._job = ProcessingJob(1, "input.pdf", app._collect_processing_options())
    app._set_status("status_preparing")
    called = []
    app._ui_messages.put((old_id, "download", (lambda: called.append(True),)))
    app._poll_work()
    assert called == []
    assert app.status_var.get() == app.strings["status_preparing"]


@pytest.mark.parametrize("operation", ["processing", "cancelling", "download"])
def test_language_change_keeps_live_status_and_button_action(app, operation):
    app.app_settings.settings.update(version="2.1.0", newest_version_available="2.1.0")
    app.update_all_widget_texts = app._render_operation
    app.update_version_labels_text = lambda *args: None
    app.update_version_labels = lambda: None
    app.root.update_idletasks = lambda: None
    for owner in (app.filter_dialog, app.watermark_dialog, app.dev_tools):
        owner.refresh_ui_strings = lambda: None
    if operation == "download":
        app.start_download()
        expected_command = app.abort_download
    else:
        app._job = ProcessingJob(1, "input.pdf", app._collect_processing_options())
        app._set_status("status_saving")
        expected_command = app.request_cancel_processing
        if operation == "cancelling":
            app.request_cancel_processing()
    status = app._status
    app.on_language_change("de")
    assert app._status == status
    assert app.status_var.get() == app.strings[status[0]]
    assert app.start_abort_button["command"] == expected_command
    assert app.start_abort_button["text"] == app.strings["btn_cancelling" if operation == "cancelling" else "btn_abort"]


def test_preflight_ocr_callbacks_do_not_overwrite_active_download(app):
    app.start_download()
    status = app.status_var.get()
    app._set_ocr_checking_status("input.pdf")
    app._apply_ocr_detection_result("input.pdf", True)
    assert app.status_var.get() == status
    assert app.ocr_detection_result is True


@pytest.mark.parametrize("operation", ["processing", "download"])
def test_reset_handler_rejects_busy_operation_without_prompting(app, monkeypatch, operation):
    from src.gui.dev_tools import DevToolsWindow

    if operation == "download":
        app.start_download()
    else:
        app._job = ProcessingJob(1, "input.pdf", app._collect_processing_options())
    monkeypatch.setattr("src.gui.dev_tools.ask_ok_cancel", lambda *args: pytest.fail("Busy reset prompted"))
    DevToolsWindow(app)._reset_settings()



def test_file_selection_prepares_preview_immediately_without_opening_dialog(app, monkeypatch):
    prepared = []
    app.marking_preview = SimpleNamespace(prepare=prepared.append)
    app.pdf_file_var = Var()
    app.root.update_idletasks = lambda: None
    app._prefetch_preview = lambda: prepared.append(("query", app.input_file_full_path))
    monkeypatch.setattr(main_window.filedialog, "askopenfilename", lambda **kwargs: "new.pdf")
    app.browse_file()
    assert prepared == ["new.pdf", ("query", "new.pdf")]


@pytest.mark.parametrize("ocr", [False, True])
def test_target_cancel_runs_no_ocr_pages_or_save_and_preserves_last_success(app, monkeypatch, ocr):
    app.start_processing()
    finish(app, app._job)
    previous = app._result_job
    previous_text = app._result_text()
    app.app_settings.settings["ocr_enabled"] = "True" if ocr else "False"
    app.ocr_detection_path, app.ocr_detection_result = "input.pdf", ocr
    order = []
    app._confirm_ocr_processing = lambda language: order.append("confirm") or "eng"
    app._ask_output_file = lambda path: order.append("target") or None
    monkeypatch.setattr(main_window, "create_searchable_ocr_pdf_in_process", lambda *a, **k: pytest.fail("OCR started"))
    monkeypatch.setattr(main_window, "highlight_matching_data", lambda **k: pytest.fail("Pages started"))
    monkeypatch.setattr(main_window, "save_compact_pdf", lambda *a: pytest.fail("Save started"))
    app.start_processing()
    finish(app, app._job)
    assert order == (["confirm", "target"] if ocr else ["target"])
    assert app.last_processing_result == ("aborted",)
    assert app._result_job is previous
    assert app._result_text() == previous_text
    assert app.status_var.get() == app.strings["status_aborted_processing"]


@pytest.mark.parametrize("ocr,failure", [
    (False, "write"), (False, "replace"), (False, "temp_create"),
    (True, "write"), (True, "replace"), (True, "temp_create"), (True, "prepare"),
])
def test_save_failure_retry_keeps_calculation_and_existing_output(app, monkeypatch, tmp_path, ocr, failure):
    app.app_settings.settings["ocr_enabled"] = "True" if ocr else "False"
    app.ocr_detection_path, app.ocr_detection_result = "input.pdf", ocr
    app._confirm_ocr_processing = lambda language: "eng"
    monkeypatch.setattr(main_window, "ensure_bundled_tessdata", lambda *a: "tessdata")
    ocr_calls, page_calls = [], []
    monkeypatch.setattr(main_window, "create_searchable_ocr_pdf_in_process", lambda *a, **k: ocr_calls.append(a))
    monkeypatch.setattr(main_window, "highlight_matching_data", lambda **k: page_calls.append(k) or (3, 0, 1))
    output = Path(app.output)
    output.write_bytes(b"existing output")
    def normal_save(doc, path):
        Path(path).write_bytes(b"calculated PDF")

    def ocr_save(prepared, path, **kwargs):
        Path(path).write_bytes(b"calculated PDF")
    monkeypatch.setattr(main_window, "save_compact_pdf", normal_save)
    monkeypatch.setattr(main_window, "save_pdf_path_in_process", ocr_save)
    original_replace = main_window.os.replace
    original_temp = main_window.tempfile.NamedTemporaryFile
    if failure == "write":
        def fail_save(*args, **kwargs):
            Path(args[1]).write_bytes(b"partial")
            raise PermissionError("disk full")
        monkeypatch.setattr(main_window, "save_pdf_path_in_process" if ocr else "save_compact_pdf", fail_save)
    elif failure == "replace":
        monkeypatch.setattr(main_window.os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("locked")))
    elif failure == "temp_create":
        def fail_temp(*args, **kwargs):
            if kwargs.get("dir"):
                raise PermissionError("no destination access")
            return original_temp(*args, **kwargs)
        monkeypatch.setattr(main_window.tempfile, "NamedTemporaryFile", fail_temp)
    else:
        # Failure to serialize OCR annotations must retain the live calculation too.
        monkeypatch.setattr(main_window.Document, "save",
                            lambda *a: (_ for _ in ()).throw(OSError("prepare failed")))
    app.start_processing()
    job = app._job
    finish(app, job)
    assert job.result[0] == "save_failed"
    assert app._pending_save is job
    assert not job.document.closed
    assert output.read_bytes() == b"existing output"
    assert list(tmp_path.glob(".hsph-*.pdf")) == []
    assert "retained" in app._result_text()
    assert app.start_abort_button["state"] == "disabled"
    app.start_processing()
    assert app._job is None  # A fresh run needs an explicit discard first.
    monkeypatch.setattr(main_window.os, "replace", original_replace)
    monkeypatch.setattr(main_window.tempfile, "NamedTemporaryFile", original_temp)
    monkeypatch.setattr(main_window, "save_compact_pdf", normal_save)
    monkeypatch.setattr(main_window, "save_pdf_path_in_process", ocr_save)
    monkeypatch.setattr(main_window.Document, "save", lambda *a: None)
    retained, backing = job.document, job.ocr_temp_file
    app.retry_save()
    finish(app, job)
    assert job.result[0] == "saved"
    assert job.result[1:3] == (6, 0)
    assert job.result[5] == 2  # Same coordinates on different pages count separately.
    assert len(page_calls) == 2 and len(ocr_calls) == int(ocr)
    assert retained.closed
    assert not backing or not Path(backing).exists()
    assert app._pending_save is None
    assert output.read_bytes() == b"calculated PDF"
    assert list(tmp_path.glob(".hsph-*.pdf")) == []
    assert app.infos == []


def fail_to_save(app, monkeypatch):
    monkeypatch.setattr(main_window, "save_compact_pdf", lambda *a: (_ for _ in ()).throw(OSError("locked")))
    app.start_processing()
    job = app._job
    finish(app, job)
    assert app._pending_save is job
    return job


def test_retry_target_cancel_preserves_pending_then_new_location_saves(app, monkeypatch, tmp_path):
    job = fail_to_save(app, monkeypatch)
    document, result = job.document, job.result
    app._ask_output_file = lambda path: None
    app.retry_save(choose_target=True)
    assert app._pending_save is job and app._job is None
    assert job.document is document and job.result == result
    output = tmp_path / "new folder" / "output with spaces.pdf"
    output.parent.mkdir()
    app._ask_output_file = lambda path: str(output)
    monkeypatch.setattr(main_window, "highlight_matching_data", lambda **k: pytest.fail("Reprocessed pages"))
    monkeypatch.setattr(main_window, "save_compact_pdf", lambda doc, path: Path(path).write_bytes(b"PDF"))
    app.retry_save(choose_target=True)
    finish(app, job)
    assert job.result[0] == "saved" and job.result[6] == str(output)
    assert output.read_bytes() == b"PDF"


@pytest.mark.parametrize("action", ["discard", "close", "cancel_retry"])
def test_retained_document_and_ocr_file_cleanup(app, monkeypatch, tmp_path, action):
    job = fail_to_save(app, monkeypatch)
    backing = tmp_path / "retained OCR.pdf"
    backing.write_bytes(b"OCR")
    job.ocr_temp_file = str(backing)
    doc = job.document
    if action == "close":
        app.close()
    elif action == "discard":
        app.discard_pending_save()
    else:
        entered, release = threading.Event(), threading.Event()
        def save(*a, **kw):
            entered.set()
            assert release.wait(2)
            raise main_window.OcrCancelled()
        monkeypatch.setattr(main_window, "save_pdf_path_in_process", save)
        app.retry_save()
        assert entered.wait(2)
        try:
            app.request_cancel_processing()
            assert not doc.closed and backing.exists()
        finally:
            release.set()
        finish(app, job)
    assert doc.closed and not backing.exists()
    assert app._pending_save is None
    assert job.result == ("aborted",)


@pytest.mark.parametrize("alias", ["same", "relative", "hardlink"])
def test_source_cannot_be_used_as_target(tmp_path, monkeypatch, alias):
    source = tmp_path / "source.pdf"
    source.write_bytes(b"source")
    if alias == "same":
        target = source
    elif alias == "relative":
        target = tmp_path / "sub" / ".." / "source.pdf"
    else:
        target = tmp_path / "linked.pdf"
        target.hardlink_to(source)
    with pytest.raises(ValueError, match="result_source_target"):
        PDFHighlighterApp._validate_output_target(source, target)
    assert source.read_bytes() == b"source"


def test_destination_selection_rejects_source_and_requires_overwrite_approval(app, monkeypatch, tmp_path):
    del app._ask_output_file  # Exercise the real selection loop.
    source, target = tmp_path / "source.pdf", tmp_path / "existing.pdf"
    source.write_bytes(b"source")
    target.write_bytes(b"old")
    selections = iter([str(source), str(target), str(target)])
    approvals = iter([False, True])
    calls = []
    def choose(**kwargs):
        assert kwargs["confirmoverwrite"] is False
        calls.append("select")
        return next(selections)
    monkeypatch.setattr(main_window.filedialog, "asksaveasfilename", choose)
    monkeypatch.setattr(main_window, "ask_ok_cancel", lambda *a: next(approvals))
    assert app._ask_output_file(str(source)) == str(target)
    assert len(calls) == 3 and len(app.errors) == 1
    assert target.read_bytes() == b"old" and source.read_bytes() == b"source"


def test_changed_destination_is_never_silently_overwritten_on_retry(app, monkeypatch):
    output = Path(app.output)
    output.write_bytes(b"approved")
    def save(doc, path):
        Path(path).write_bytes(b"result")
        output.write_bytes(b"another application changed this")
    monkeypatch.setattr(main_window, "save_compact_pdf", save)
    app.start_processing()
    job = app._job
    finish(app, job)
    assert job.result[0] == "save_failed"
    assert output.read_bytes() == b"another application changed this"
    monkeypatch.setattr(main_window, "save_compact_pdf", lambda doc, path: Path(path).write_bytes(b"result"))
    app.retry_save()
    finish(app, job)
    assert job.result[0] == "save_failed"
    assert output.read_bytes() == b"another application changed this"
    app.retry_save(choose_target=True)  # Re-selection represents renewed overwrite approval.
    finish(app, job)
    assert job.result[0] == "saved" and output.read_bytes() == b"result"


def test_committed_save_remains_successful_when_cancellation_arrives_after_replace(app, monkeypatch):
    original = main_window.os.replace
    def replace(source, target):
        original(source, target)
        app._job.cancel.set()
    monkeypatch.setattr(main_window.os, "replace", replace)
    app.start_processing()
    finish(app, app._job)
    assert app.last_processing_result[0] == "saved"
    assert "Finished" in app._result_text()


def test_opening_actions_pass_complete_paths_and_handle_missing_or_failed_open(app, monkeypatch, tmp_path, caplog):
    output = tmp_path / "folder with spaces" / "result with spaces.pdf"
    output.parent.mkdir()
    output.write_bytes(b"PDF")
    app._ask_output_file = lambda path: str(output)
    app.start_processing()
    finish(app, app._job)
    opened = []
    if main_window.os.name == "nt":
        monkeypatch.setattr(main_window.os, "startfile", opened.append)
    else:
        monkeypatch.setattr(main_window.subprocess, "Popen", lambda args: opened.append(args[-1]))
    app.open_result_pdf()
    app.open_result_folder()
    assert opened == [str(output), str(output.parent)]
    def fail(*args):
        raise OSError("no associated app")
    if main_window.os.name == "nt":
        monkeypatch.setattr(main_window.os, "startfile", fail)
    else:
        monkeypatch.setattr(main_window.subprocess, "Popen", fail)
    app.open_result_pdf()
    assert app.errors[-1][1] == app.strings["result_open_failed"]
    assert "no associated app" in caplog.text
    output.unlink()
    app.open_result_folder()
    assert app.errors[-1][1] == app.strings["result_open_missing"]
    assert opened == [str(output), str(output.parent)]


@pytest.mark.parametrize("kind", ["save_failed", "failed", "aborted", "info_key"])
def test_open_actions_ignore_unsaved_results(app, monkeypatch, kind):
    job = ProcessingJob(1, "source.pdf", app._collect_processing_options())
    job.result = (kind, "val_nothing")
    app._result_job = job
    monkeypatch.setattr(main_window.os, "startfile", lambda *a: pytest.fail("Opened unsaved PDF"), raising=False)
    monkeypatch.setattr(main_window.subprocess, "Popen", lambda *a: pytest.fail("Opened unsaved PDF"))
    app.open_result_pdf()
    app.open_result_folder()


@pytest.mark.parametrize("ocr", [False, True])
def test_real_pdf_retry_preserves_source_counts_and_annotations(app, monkeypatch, tmp_path, ocr):
    import shutil
    from src.core.pdf_processor import highlight_matching_data
    from src.core.ocr import save_compact_pdf
    source = tmp_path / "source.pdf"
    with PdfDocument() as document:
        for _ in range(2):
            page = document.new_page()
            page.insert_text((50, 60), "Bahn 1 CLUB Alice CLUB 00:59.99")
            page.add_text_annot((400, 50), "Preserve existing annotation")
        document.save(source)
    source_bytes = source.read_bytes()
    output = Path(app.output)
    output.write_bytes(b"existing")
    app.input_file_full_path = str(source)
    app.app_settings.settings["ocr_enabled"] = "True" if ocr else "False"
    app.ocr_detection_path, app.ocr_detection_result = str(source), ocr
    app._confirm_ocr_processing = lambda lang: "eng"
    calls = []
    def copy_ocr(input_path, output_path, **kwargs):
        calls.append("ocr")
        shutil.copyfile(input_path, output_path)
    def save_process(prepared, target, **kwargs):
        with PdfDocument(prepared) as document:
            save_compact_pdf(document, target)
    monkeypatch.setattr(main_window, "Document", PdfDocument)
    monkeypatch.setattr(main_window, "highlight_matching_data", highlight_matching_data)
    monkeypatch.setattr(main_window, "save_compact_pdf", save_compact_pdf)
    monkeypatch.setattr(main_window, "ensure_bundled_tessdata", lambda *a: "tessdata")
    monkeypatch.setattr(main_window, "create_searchable_ocr_pdf_in_process", copy_ocr)
    monkeypatch.setattr(main_window, "save_pdf_path_in_process", save_process)
    original_replace = main_window.os.replace
    monkeypatch.setattr(main_window.os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("locked output")))
    app.start_processing()
    job = app._job
    finish(app, job)
    assert job.result[0] == "save_failed"
    assert output.read_bytes() == b"existing"
    assert source.read_bytes() == source_bytes
    monkeypatch.setattr(main_window.os, "replace", original_replace)
    monkeypatch.setattr(main_window, "highlight_matching_data", lambda **k: pytest.fail("Repeated marking"))
    app.retry_save()
    finish(app, job)
    assert job.result[:3] == ("saved", 4, 0)
    assert job.result[5] == 2
    assert calls == (["ocr"] if ocr else [])
    assert source.read_bytes() == source_bytes
    with PdfDocument(output) as document:
        for page in document:
            annotations = list(page.annots())
            assert len(annotations) == 2
            assert annotations[0].info["content"] == "Preserve existing annotation"
    assert list(tmp_path.glob(".hsph-*.pdf")) == []


def test_ocr_confirmation_cancel_never_asks_target_or_starts_processing(app, monkeypatch):
    app.app_settings.settings["ocr_enabled"] = "True"
    app.ocr_detection_path, app.ocr_detection_result = "input.pdf", True
    app._confirm_ocr_processing = lambda lang: None
    app._ask_output_file = lambda *a: pytest.fail("Target offered after cancelled OCR")
    monkeypatch.setattr(main_window, "create_searchable_ocr_pdf_in_process", lambda *a, **kw: pytest.fail("OCR"))
    app.start_processing()
    finish(app, app._job)
    assert app.last_processing_result == ("aborted",)


@pytest.mark.parametrize("invalid", ["path", "encrypted", "required"])
def test_preflight_rejects_invalid_input_before_target_selection(app, monkeypatch, invalid):
    app._ask_output_file = lambda *a: pytest.fail("Target offered for invalid source")
    if invalid == "path":
        app.paths.is_valid_path = lambda path: (_ for _ in ()).throw(FileNotFoundError("source missing"))
    elif invalid == "encrypted":
        monkeypatch.setattr(main_window.Document, "is_encrypted", True)
    else:
        app.search_phrase_var.set("   ")
    app.start_processing()
    if invalid == "required":
        assert app._job is None and app.errors
    else:
        finish(app, app._job)
        assert app.last_processing_result[0] in ("failed", "error_key")


def test_fsync_failure_retains_result_and_does_not_replace_output(app, monkeypatch):
    output = Path(app.output)
    output.write_bytes(b"existing")
    monkeypatch.setattr(main_window.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("sync failure")))
    app.start_processing()
    finish(app, app._job)
    assert app._pending_save and output.read_bytes() == b"existing"
    assert list(output.parent.glob(".hsph-*.pdf")) == []


def test_retry_thread_start_failure_preserves_pending_calculation(app, monkeypatch, caplog):
    job = fail_to_save(app, monkeypatch)
    document = job.document
    monkeypatch.setattr(threading.Thread, "start", lambda self: (_ for _ in ()).throw(RuntimeError("thread start failed")))
    app.retry_save()
    assert app._pending_save is job and app._job is None
    assert job.document is document and not document.closed
    assert "thread start failed" not in app._result_text()
    assert "thread start failed" in caplog.text


@pytest.mark.parametrize("close", [False, True])
def test_retry_target_nested_event_loop_keeps_job_reserved(app, monkeypatch, close):
    job = fail_to_save(app, monkeypatch)
    document = job.document
    def choose(path):
        app._poll_work()
        assert app._job is job and job.dialog_open
        assert app.start_download() is False
        assert not document.closed
        if close:
            app.close()
        return None
    app._ask_output_file = choose
    app.retry_save(choose_target=True)
    assert app._job is None
    assert document.closed is close
    assert (app._pending_save is None) is close


@pytest.mark.parametrize("kind", ["failed", "save_failed"])
@pytest.mark.parametrize("detail", ["RuntimeError: worker exited with code 1; hsph-ocr-secret.pdf", "upd_note"])
def test_user_messages_do_not_include_internal_failure_details(app, kind, detail):
    job = ProcessingJob(1, "source.pdf", app._collect_processing_options(), started=True)
    job.result = (kind, detail)
    app._job = job
    app.finalize_processing(job)
    assert detail not in app._result_text()
    assert detail not in app.status_var.get()
    assert all(detail not in message for _, message in app.errors)
    assert app._result_text() == app.strings["result_save_failed" if kind == "save_failed" else "result_processing_failed"]
