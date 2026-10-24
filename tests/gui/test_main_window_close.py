"""Close requests leave Tk alive until owned work has actually ended."""

import threading

from test_main_window_processing import app as app
from src.gui import message_dialog


def test_close_is_idempotent_and_destroys_root_on_next_poll(app):
    app.close()
    app.close()
    assert app._closing
    assert not app.root.destroyed
    app._poll_work()
    assert app.root.destroyed


def test_close_cancels_job_and_waits_without_joining_in_tk(app, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def worker(job):
        entered.set()
        assert release.wait(2)
        assert job.cancel.is_set()
        app._post_job(job, "result", "aborted")

    monkeypatch.setattr(app, "process_pdf", worker)
    app.start_processing()
    job = app._job
    try:
        assert entered.wait(2)
        app.close()
        app._poll_work()
        assert app._job is job
        assert not app.root.destroyed
        app.start_processing()
        assert app._job is job
    finally:
        release.set()
        job.worker.join(2)
    app._poll_work()
    assert app.root.destroyed
    assert app.infos == app.errors == []


def test_close_waits_for_download_cleanup(app):
    assert app.start_download()
    app.update_checker._active_download = True
    app.close()
    app._poll_work()
    assert app.is_download_aborted()
    assert not app.root.destroyed
    app.update_checker._active_download = False
    app._poll_work()
    assert app.root.destroyed


def test_close_releases_unscheduled_message_dialog_waiter(app):
    entered = threading.Event()
    original_after = app.root.after

    def after(*args):
        original_after(*args)
        entered.set()

    app.root.after = after
    worker = threading.Thread(target=lambda: message_dialog._run_on_tk(app, lambda: None))
    worker.start()
    assert entered.wait(2)
    app.close()
    worker.join(2)
    assert not worker.is_alive()



def test_close_waits_for_detached_preview_cleanup(app):
    release = threading.Event()
    worker = threading.Thread(target=lambda: release.wait(2))
    worker.start()
    app._preview_cleanups = [worker]
    try:
        app.close()
        app._poll_work()
        assert not app.root.destroyed
    finally:
        release.set()
        worker.join(2)
    app._poll_work()
    assert app.root.destroyed



def test_close_tolerates_windows_that_have_already_been_destroyed(app):
    from tkinter import TclError
    from types import SimpleNamespace

    def destroyed():
        raise TclError("invalid command name")

    app.preview_window_handler.close = destroyed
    app.dev_tools.window = SimpleNamespace(destroy=destroyed)
    app.close()
    app._poll_work()
    assert app.root.destroyed
