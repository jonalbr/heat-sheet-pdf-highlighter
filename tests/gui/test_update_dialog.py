"""Drive the real themed modal dialog, including its worker-thread entry."""

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


def drive(app, callback):
    errors = []

    def interact():
        window = next(child for child in app.root.winfo_children() if isinstance(child, tk.Toplevel))
        try:
            callback(window)
        except BaseException as error:
            errors.append(error)
            window.destroy()

    app.root.after(30, interact)
    result = UpdateDialogs(app).show_update_available(Version.from_str("99.0.0"))
    if errors:
        raise errors[0]
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
            window.focus_force()
            window.event_generate("<Escape>")
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
        button.focus_force()
        button.event_generate("<Return>")

    assert drive(app, interact) == UpdateDecision(action)


def test_worker_entry_executes_dialog_on_main_thread(app, monkeypatch):
    result, calls, workers = [], [], []
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
            result.append(UpdateDialogs(app).show_update_available(Version.from_str("99.0.0")))

        worker = threading.Thread(target=work)
        workers.append(worker)
        worker.start()

    def poll():
        if result:
            app.root.quit()
        else:
            app.root.after(10, poll)

    app.root.after(0, start)
    app.root.after(10, poll)
    app.root.after(3000, app.root.quit)
    app.root.mainloop()
    workers[0].join(2)
    assert not workers[0].is_alive()
    assert result == [UpdateDecision("cancel")]
    assert calls == [threading.main_thread()]


def test_closing_or_obsolete_request_never_opens_dialog(app):
    assert UpdateDialogs(app).show_update_available(Version(), lambda: False) == UpdateDecision("cancel")
    app._closing = True
    assert UpdateDialogs(app).show_update_available(Version()) == UpdateDecision("cancel")
    assert app.themed_threads == []
