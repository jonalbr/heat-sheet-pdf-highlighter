"""Deterministic job races with queued Tk callbacks and controlled workers."""

import queue
import threading
from types import SimpleNamespace

import pytest

from src.gui import main_window
from src.gui.main_window import GuiRequest, PDFHighlighterApp, ProcessingJob
from src.gui.ui_strings import build_strings, plural_strings


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
def app(monkeypatch):
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
    app._ask_output_file = lambda path: "out.pdf"
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
    monkeypatch.setattr(main_window, "highlight_matching_data", lambda **kwargs: (1, 0))
    monkeypatch.setattr(main_window, "watermark_pdf_page", lambda *args: None)
    monkeypatch.setattr(main_window, "save_compact_pdf", lambda *args: None)
    yield app
    if app._job and app._job.worker:
        app._job.cancel.set()
        app._job.worker.join(timeout=2)
        assert not app._job.worker.is_alive()


def finish(app, job):
    """Wait for messages, not elapsed time, while serving main-thread requests."""
    while job.result is None:
        message = app._ui_messages.get(timeout=2)
        app._ui_messages.put(message)
        app._poll_work()
    job.worker.join(timeout=2)
    assert not job.worker.is_alive()
    app._poll_work()


def test_snapshot_is_used_between_pages_and_worker_never_reads_tk(app, monkeypatch):
    highlights, watermarks, saved = [], [], []
    first_page, next_page = threading.Event(), threading.Event()

    def highlight(**kwargs):
        highlights.append(kwargs)
        if len(highlights) == 1:
            first_page.set()
            assert next_page.wait(2)
        return 1, 0

    monkeypatch.setattr(main_window, "highlight_matching_data", highlight)
    monkeypatch.setattr(main_window, "watermark_pdf_page", lambda page, settings: watermarks.append(dict(settings)))
    monkeypatch.setattr(main_window, "save_compact_pdf", lambda doc, path: saved.append(path))
    app.start_processing()
    job = app._job
    try:
        assert first_page.wait(2)
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
    assert saved == ["out.pdf"]
    assert app.last_processing_result[0] == "saved"
    assert all(doc.closed for doc in app.documents)
    assert app.infos[0][0] == "Finished"


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
        assert entered.wait(2)
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
        return 1, 0

    monkeypatch.setattr(main_window, "highlight_matching_data", highlight)
    app.start_processing()
    job = app._job
    try:
        assert entered.wait(2)
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
    monkeypatch.setattr(main_window, "save_pdf_path_in_process", lambda *args, **kwargs: save_calls.append(kwargs))
    app.start_processing()
    job = app._job
    finish(app, job)
    assert ocr_calls[0]["dpi"] == 300
    assert ocr_calls[0]["language"] == "eng"
    assert save_calls[0]["reduce_large_outputs"] is True
    assert save_calls[0]["is_cancelled"].__self__ is job.cancel
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


def test_worker_error_closes_document_and_reports_after_exit(app, monkeypatch):
    monkeypatch.setattr(main_window, "highlight_matching_data", lambda **kwargs: (_ for _ in ()).throw(ValueError("broken")))
    app.start_processing()
    job = app._job
    finish(app, job)
    assert app.last_processing_result == ("failed", "broken")
    assert app.errors[0][1] == "broken"
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
