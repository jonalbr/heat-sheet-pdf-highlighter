"""
Theme-aware modal dialogs for app-owned popups.
"""

from __future__ import annotations

from contextlib import suppress
import threading
from typing import TYPE_CHECKING, Any
from tkinter import TclError, Toplevel, ttk

from .ui_strings import get_ui_string

if TYPE_CHECKING:
    from src.gui.main_window import PDFHighlighterApp


ButtonSpec = tuple[str, Any]


def show_info(app: "PDFHighlighterApp", title: str, message: str) -> None:
    """Show a themed informational dialog."""
    _run_on_tk(app, lambda: _show_dialog(app, title, message, [(_button_label(app, "btn_ok", "OK"), True)]))


def show_error(app: "PDFHighlighterApp", title: str, message: str) -> None:
    """Show a themed error dialog."""
    _run_on_tk(app, lambda: _show_dialog(app, title, message, [(_button_label(app, "btn_ok", "OK"), True)]))


def ask_ok_cancel(app: "PDFHighlighterApp", title: str, message: str) -> bool:
    """Ask for OK/Cancel with a themed dialog."""
    return bool(
        _run_on_tk(
            app,
            lambda: _show_dialog(
                app,
                title,
                message,
                [(_button_label(app, "btn_ok", "OK"), True), (_button_label(app, "btn_cancel", "Cancel"), False)],
                cancel_value=False,
            ),
        )
    )


def ask_retry_cancel(app: "PDFHighlighterApp", title: str, message: str) -> bool:
    """Ask for Retry/Cancel with a themed dialog."""
    return bool(
        _run_on_tk(
            app,
            lambda: _show_dialog(
                app,
                title,
                message,
                [(_button_label(app, "btn_retry", "Retry"), True), (_button_label(app, "btn_cancel", "Cancel"), False)],
                cancel_value=False,
            ),
        )
    )


def ask_yes_no_cancel(app: "PDFHighlighterApp", title: str, message: str) -> bool | None:
    """Ask for Yes/No/Cancel with a themed dialog."""
    return _run_on_tk(
        app,
        lambda: _show_dialog(
            app,
            title,
            message,
            [
                (_button_label(app, "btn_yes", "Yes"), True),
                (_button_label(app, "btn_no", "No"), False),
                (_button_label(app, "btn_cancel", "Cancel"), None),
            ],
            cancel_value=None,
        ),
    )


def _button_label(app: "PDFHighlighterApp", key: str, default: str) -> str:
    return get_ui_string(app.strings, key, default=default)


def _run_on_tk(app: "PDFHighlighterApp", callback):
    if threading.current_thread() is threading.main_thread():
        return callback()

    done = threading.Event()
    result: dict[str, Any] = {"value": None, "error": None}

    def invoke():
        try:
            result["value"] = callback()
        except Exception as exc:  # pragma: no cover - defensive UI marshalling
            result["error"] = exc
        finally:
            done.set()

    try:
        app.root.after(0, invoke)
    except Exception:
        return None

    done.wait()
    if result["error"] is not None:
        raise result["error"]
    return result["value"]


def _show_dialog(
    app: "PDFHighlighterApp",
    title: str,
    message: str,
    buttons: list[ButtonSpec],
    cancel_value: Any = True,
) -> Any:
    parent = app.root
    result = {"value": cancel_value}

    window = Toplevel(parent)
    window.title(title)
    window.transient(parent)
    window.resizable(False, False)
    app.apply_theme_to_window(window)

    def close(value: Any = cancel_value) -> None:
        result["value"] = value
        with suppress(TclError):
            window.grab_release()
        window.destroy()

    window.protocol("WM_DELETE_WINDOW", lambda: close(cancel_value))

    frame = ttk.Frame(window, padding=(22, 18, 22, 16))
    frame.grid(row=0, column=0, sticky="nsew")
    frame.grid_columnconfigure(0, weight=1)

    ttk.Label(frame, text=title, font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
    ttk.Label(frame, text=message, justify="left", wraplength=390).grid(row=1, column=0, sticky="we", pady=(10, 18))

    button_frame = ttk.Frame(frame)
    button_frame.grid(row=2, column=0, sticky="e")

    default_button = None
    for index, (label, value) in enumerate(buttons):
        button = ttk.Button(button_frame, text=label, command=lambda v=value: close(v), width=10)
        button.grid(row=0, column=index, padx=(8 if index else 0, 0))
        if index == 0:
            default_button = button

    app.apply_theme_to_window(window)
    _center_window(window, parent)

    if default_button is not None:
        default_button.focus_set()
        window.bind("<Return>", lambda _event: default_button.invoke())
    window.bind("<Escape>", lambda _event: close(cancel_value))

    window.grab_set()
    window.wait_window()
    return result["value"]


def _center_window(window: Toplevel, parent) -> None:
    window.update_idletasks()
    width = window.winfo_width()
    height = window.winfo_height()

    try:
        parent.update_idletasks()
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_width = parent.winfo_width()
        parent_height = parent.winfo_height()
        x = parent_x + max((parent_width - width) // 2, 0)
        y = parent_y + max((parent_height - height) // 2, 0)
    except Exception:
        x = window.winfo_screenwidth() // 2 - width // 2
        y = window.winfo_screenheight() // 2 - height // 2

    window.geometry(f"+{x}+{y}")
