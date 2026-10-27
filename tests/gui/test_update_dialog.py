"""Drive the real themed modal dialog, including its worker-thread entry."""

from contextlib import contextmanager
import threading
from types import SimpleNamespace
import tkinter as tk
from tkinter import ttk

import pytest

from src.config.paths import Paths
from src.gui.dialogs import UpdateDialogs
from src.gui import message_dialog
from src.gui.ui_strings import build_strings
from src.models import UpdateDecision
from src.utils.localization import setup_translation
from src.version import Version


@pytest.fixture(params=[("de", "breeze"), ("en", "breeze-dark")])
def app(request):
    root = tk.Tk()
    root.geometry("600x300+20+20")
    root.tk.call("lappend", "auto_path", str(Paths.tcl_lib_path))
    root.tk.call("package", "require", f"ttk::theme::{request.param[1]}")
    ttk.Style(root).theme_use(request.param[1])
    themed_threads = []
    instance = SimpleNamespace(
        root=root, _closing=False,
        strings=build_strings(setup_translation(request.param[0])[0]),
        apply_theme_to_window=lambda window: themed_threads.append(threading.current_thread()),
        themed_threads=themed_threads,
    )
    root.update()
    yield instance
    for timer in root.tk.call("after", "info"):
        root.after_cancel(timer)
    root.destroy()


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


@contextmanager
def dialog_guard(app, timeout_ms=5000):
    """Fail and unwind nested Tk waits on timeout or callback exceptions."""
    errors = []
    root = app.root
    original_report = root.report_callback_exception
    original_closing = app._closing
    original_timers = set(root.tk.call("after", "info"))

    def abort(error):
        errors.append(error)
        app._closing = True
        for child in root.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.destroy()
        # quit alone does not release wait_window's nested event loop.
        root.quit()

    root.report_callback_exception = lambda _kind, error, traceback: abort(error.with_traceback(traceback))
    root.after(timeout_ms, lambda: abort(AssertionError(f"Update dialog timed out after {timeout_ms} ms")))
    try:
        yield
    finally:
        for timer in set(root.tk.call("after", "info")) - original_timers:
            root.after_cancel(timer)
        root.report_callback_exception = original_report
        if errors:
            app._closing = original_closing
            raise errors[0]


def send_key_when_focused(widget, key):
    """Allow mapping and native focus events to finish before sending a key."""
    def send():
        if not widget.winfo_viewable():
            widget.after(10, send)
        elif widget.focus_get() != widget:
            widget.focus_force()
            widget.after(10, send)
        else:
            widget.event_generate(key, when="tail")

    widget.after_idle(send)


def drive(app, callback, *, timeout_ms=5000):
    """Drive a modal dialog with bounded interaction and reliable cleanup."""

    def interact():
        window = next(child for child in app.root.winfo_children() if isinstance(child, tk.Toplevel))
        callback(window)

    with dialog_guard(app, timeout_ms):
        app.root.after(30, interact)
        result = UpdateDialogs(app).show_update_available(Version.from_str("99.0.0"))
    return result


@pytest.mark.parametrize("action,key", [("update_now", "upd_now"), ("later", "upd_later")])
@pytest.mark.parametrize("checked", [False, True])
def test_both_buttons_return_action_and_checkbox(app, action, key, checked):
    def interact(window):
        checkbox = next(widget for widget in descendants(window) if isinstance(widget, ttk.Checkbutton))
        assert not checkbox.instate(["selected"])
        assert not checkbox.instate(["disabled"])
        assert checkbox["text"] == app.strings["upd_skip_version"]
        assert window.grab_current() == window
        assert "99.0.0" in " ".join(str(widget.cget("text")) for widget in descendants(window) if isinstance(widget, ttk.Label))
        if checked:
            checkbox.invoke()
        button = next(widget for widget in descendants(window) if isinstance(widget, ttk.Button) and widget["text"] == app.strings[key])
        # Natural button width must fit the full translated label.
        assert button.winfo_width() >= button.winfo_reqwidth()
        button.invoke()

    assert drive(app, interact) == UpdateDecision(action, checked)
    assert all(thread is threading.main_thread() for thread in app.themed_threads)


@pytest.mark.parametrize("close_way", ["escape", "window", "app"])
def test_dismissal_discards_checked_draft(app, close_way):
    def interact(window):
        next(widget for widget in descendants(window) if isinstance(widget, ttk.Checkbutton)).invoke()
        if close_way == "window":
            window.tk.call(window.protocol("WM_DELETE_WINDOW"))
        elif close_way == "escape":
            send_key_when_focused(window, "<Escape>")
        else:
            app._closing = True
            window.destroy()  # Main app close destroys outstanding Toplevels.

    assert drive(app, interact) == UpdateDecision("cancel")
    assert app.root.grab_current() is None


def test_reopening_starts_with_unchecked_checkbox(app):
    def choose(window):
        checkbox = next(widget for widget in descendants(window) if isinstance(widget, ttk.Checkbutton))
        assert not checkbox.instate(["selected"])
        checkbox.invoke()
        window.tk.call(window.protocol("WM_DELETE_WINDOW"))

    assert drive(app, choose) == drive(app, choose) == UpdateDecision("cancel")


@pytest.mark.parametrize("key,action", [("upd_now", "update_now"), ("upd_later", "later")])
def test_return_activates_focused_action(app, key, action):
    def interact(window):
        button = next(widget for widget in descendants(window) if isinstance(widget, ttk.Button) and widget["text"] == app.strings[key])
        send_key_when_focused(button, "<Return>")

    assert drive(app, interact) == UpdateDecision(action)


def test_worker_entry_executes_dialog_on_main_thread(app, monkeypatch):
    result, calls, workers, errors = [], [], [], []
    original = message_dialog._show_update_dialog

    def show(*args):
        calls.append(threading.current_thread())
        app.root.after(30, lambda: next(
            child for child in app.root.winfo_children() if isinstance(child, tk.Toplevel)
        ).destroy())
        return original(*args)

    monkeypatch.setattr(message_dialog, "_show_update_dialog", show)

    def start():
        def work():
            try:
                result.append(UpdateDialogs(app).show_update_available(Version.from_str("99.0.0")))
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=work)
        workers.append(worker)
        worker.start()

    def poll():
        if result or errors:
            app.root.quit()
        else:
            app.root.after(10, poll)

    try:
        with dialog_guard(app):
            app.root.after(0, start)
            app.root.after(10, poll)
            app.root.mainloop()
    finally:
        app._closing = True
        for worker in workers:
            worker.join(2)
    if errors:
        raise errors[0]
    assert not workers[0].is_alive()
    assert result == [UpdateDecision("cancel")]
    assert calls == [threading.main_thread()]


def test_unanswered_dialog_times_out_and_releases_modal_grab(app):
    original_report = app.root.report_callback_exception
    with pytest.raises(AssertionError, match="Update dialog timed out"):
        drive(app, lambda window: None, timeout_ms=100)
    assert app.root.grab_current() is None
    assert not app.root.winfo_children()
    assert app.root.report_callback_exception == original_report
    assert not app._closing
    assert not app.root.tk.call("after", "info")


def test_unanswered_worker_dialog_times_out_and_releases_thread(app):
    result, workers = [], []

    def start():
        worker = threading.Thread(target=lambda: result.append(
            UpdateDialogs(app).show_update_available(Version.from_str("99.0.0")),
        ))
        workers.append(worker)
        worker.start()

    try:
        with pytest.raises(AssertionError, match="Update dialog timed out"):
            with dialog_guard(app, timeout_ms=1000):
                app.root.after(0, start)
                app.root.mainloop()
    finally:
        app._closing = True
        for worker in workers:
            worker.join(2)
    assert workers and not workers[0].is_alive()
    assert result == [UpdateDecision("cancel")]
    assert app.root.grab_current() is None
    assert not app.root.winfo_children()


@pytest.mark.parametrize("deferred", [False, True])
def test_interaction_errors_unwind_modal_wait(app, deferred):
    def fail():
        raise RuntimeError("Dialog interaction failed")

    def interact(window):
        if deferred:
            window.after_idle(fail)
        else:
            fail()

    with pytest.raises(RuntimeError, match="Dialog interaction failed"):
        drive(app, interact)
    assert app.root.grab_current() is None
    assert not app.root.winfo_children()


def test_closing_or_obsolete_request_never_opens_dialog(app):
    assert UpdateDialogs(app).show_update_available(Version(), lambda: False) == UpdateDecision("cancel")
    app._closing = True
    assert UpdateDialogs(app).show_update_available(Version()) == UpdateDecision("cancel")
    assert app.themed_threads == []
