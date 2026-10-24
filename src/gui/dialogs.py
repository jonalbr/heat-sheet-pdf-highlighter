"""
Dialog windows (Filter and Watermark dialogs)
"""

import time
import threading
from functools import partial
from tkinter import DoubleVar, IntVar, Menu, StringVar, TclError, Toplevel, colorchooser, ttk
from tkinter import Button as tkButton
from typing import TYPE_CHECKING, Dict, Optional
import logging

if TYPE_CHECKING:
    from src.gui.main_window import PDFHighlighterApp

from ..core.watermark import get_position_ratios
from ..config.settings import validated_highlight_color
from ..version import Version
from .message_dialog import ask_ok_cancel, ask_retry_cancel, ask_yes_no_cancel, show_error, show_info
from .ui_strings import get_ui_string
from .filter_dialog import FilterDialog as FilterDialog
from .widgets import lock_controls


class UpdateDialogs:
    """Handles all GUI interactions for the updater functionality."""

    def __init__(self, app_instance: "PDFHighlighterApp"):
        self.app = app_instance
        # Internal counters to avoid reading Tk widgets from worker threads
        self._dl_total_size: int = 0
        self._dl_downloaded_bytes: int = 0

    # --- helper to marshal work to Tk main thread ---
    def _ui(self, fn):
        identifier = getattr(self, "_download_id", 0)
        self.app._ui_messages.put((identifier, "download", (fn,)))

    def _call_main(self, callback):
        from .main_window import GuiRequest

        if threading.current_thread() is threading.main_thread():
            return callback()
        request = GuiRequest(callback)
        self.app._ui_messages.put((0, "download_request", (request,)))
        while not request.ready.wait(0.05):
            if self.app._closing:
                return False
        if request.error:
            raise request.error
        return request.value

    def show_up_to_date(self):
        """Show message that app is up to date."""
        show_info(
            self.app,
            get_ui_string(self.app.strings, "upd_ok"),
            get_ui_string(self.app.strings, "upd_latest"),
        )

    def show_update_available(self, latest_version: Version) -> Optional[bool]:
        """
        Show update available dialog.

        Returns:
            True if user wants to update
            False if user doesn't want to update
            None if user cancelled
        """
        return ask_yes_no_cancel(
            self.app,
            get_ui_string(self.app.strings, "upd_avail"),
            get_ui_string(self.app.strings, "upd_prompt").format(latest_version),
        )

    def show_update_reminder_choice(self) -> bool:
        """
        Show dialog asking if user wants to be reminded again.

        Returns:
            True if user doesn't want to be reminded again
            False if user wants to be reminded again
        """
        return ask_ok_cancel(
            self.app,
            get_ui_string(self.app.strings, "upd_info"),
            get_ui_string(
                self.app.strings,
                "upd_note",
            ),
        )

    def show_update_error_retry(self, error_message: str) -> bool:
        """
        Show update error dialog with retry option.

        Returns:
            True if user wants to retry
            False if user doesn't want to retry
        """
        return ask_retry_cancel(
            self.app,
            get_ui_string(self.app.strings, "upd_error"),
            get_ui_string(self.app.strings, "upd_check_failed").format(error_message),
        )

    def show_download_error(self, error_message: str):
        """Show download error message."""
        def report():
            show_error(self.app, get_ui_string(self.app.strings, "error"),
                       get_ui_string(self.app.strings, "upd_download_failed").format(error_message))
        if self.app.download_active:
            self._ui(report)
        else:
            from .main_window import GuiRequest

            self.app._ui_messages.put((0, "download_request", (GuiRequest(report),)))

    def setup_download_progress(self, total_size: int):
        """Setup progress bar for download and show initial label."""
        self._dl_total_size = int(total_size)
        self._dl_downloaded_bytes = 0
        if hasattr(self.app, "progress_bar"):
            def _init_progress_ui():
                try:
                    if self.is_download_cancelled():
                        return
                    self.app.progress_bar["maximum"] = self._dl_total_size
                    self._reset_progressbar_value()
                    # Show initial label for download
                    if hasattr(self.app, "status_var"):
                        self.app._set_status("upd_progress", 0, self._dl_total_size / (1024 * 1024), 0)
                except Exception as e:
                    logging.getLogger("dialogs").exception("Error initializing progress UI: %s", e)
            self._ui(_init_progress_ui)

    def _reset_progressbar_value(self):
        try:
            if hasattr(self.app, "progress_bar"):
                self.app.progress_bar["value"] = 0
            if hasattr(self.app, "status_var"):
                self.app.status_var.set("")
        except Exception as e:
            logging.getLogger("dialogs").exception("Error resetting progress bar: %s", e)

    def update_download_progress(self, data_size: int):
        """Update download progress bar."""
        # Update internal counter first; UI will catch up via scheduled lambda
        self._dl_downloaded_bytes += int(data_size)
        if hasattr(self.app, "progress_bar"):
            self._ui(lambda: self._inc_progressbar(int(data_size)))

    def _inc_progressbar(self, inc: int):
        try:
            if hasattr(self.app, "progress_bar"):
                self.app.progress_bar["value"] = min(
                    (self.app.progress_bar["value"] or 0) + inc,
                    self._dl_total_size or 0,
                )
        except Exception as e:
            logging.getLogger("dialogs").exception("Error incrementing progress bar: %s", e)

    def update_download_status(self, start_time: float, total_size: int):
        """Update download status text."""
        # Compute using internal counters to avoid reading Tk state from worker thread
        elapsed_time = max(0.001, time.time() - start_time)
        current_value = self._dl_downloaded_bytes
        total = int(total_size) if total_size else (self._dl_total_size or 0)

        if hasattr(self.app, "status_var") and current_value > 0 and total > 0:
            speed = current_value / elapsed_time
            remaining_time = (total - current_value) / speed if speed > 0 else 0
            downloaded_MB = current_value / (1024 * 1024)
            total_MB = total / (1024 * 1024)

            self._ui(lambda: self.app._set_status("upd_progress", downloaded_MB, total_MB, remaining_time) if not self.is_download_cancelled() else None)

    def close_application(self):
        self._ui(self.app.close)

    def get_progress_value(self) -> float:
        return float(self._dl_downloaded_bytes)

    def start_download_ui(self):
        """Return whether Tk granted exclusive ownership of shared controls."""
        def reserve():
            accepted = self.app.start_download()
            if accepted:
                self._download_id = self.app._download_id
            return accepted
        return bool(self._call_main(reserve))

    def is_download_cancelled(self) -> bool:
        return self.app.is_download_aborted()

    def finish_download_ui(self):
        self._ui(self.app.finish_download)



class WatermarkDialog:
    """Watermark configuration dialog."""

    def __init__(self, app_instance: "PDFHighlighterApp"):
        self.app = app_instance
        self.parent = self.app.root
        self.window = None
        self._draft_active = False
        self._busy = False
        self._traces = []
        self._draft_vars = {}
        self._dependent_controls = []
        self._stop_nudge = lambda: None

    def _editable(self):
        """Allow editing only for an enabled draft without an operation lock."""
        return (self._draft_active and not self._busy
                and not getattr(self.app, "output_options_locked", False)
                and bool(self._draft_vars["enabled"].get()))

    def _update_controls(self):
        """Reapply activation after a busy state or theme change."""
        if not self._draft_active:
            return
        active = self._editable()
        if not active:
            self._stop_nudge()
        for control in self._dependent_controls:
            control.configure(state="normal" if active else "disabled")

    def refresh_theme(self):
        """Preserve the local activation state after theming classic widgets."""
        self._update_controls()

    def _trace(self, variable, callback):
        """Track each trace so all closing paths can remove its Tcl callback."""
        self._traces.append((variable, variable.trace_add("write", callback)))

    def set_busy(self, busy):
        """Lock controls without publishing or discarding the local draft."""
        self._busy = busy
        if busy:
            self._stop_nudge()
        if self.window is not None and self.window.winfo_exists():
            lock_controls(self.window, busy, (self._cancel_button,))
            self._update_controls()

    def _draft(self):
        return {key: variable.get() for key, variable in self._draft_vars.items()}

    def _cleanup(self, close_preview=True):
        """Detach callbacks before destroying widgets or abandoning a draft."""
        self._draft_active = False
        self._stop_nudge()
        self._stop_nudge = lambda: None
        for variable, trace in self._traces:
            try:
                variable.trace_remove("write", trace)
            except TclError:
                pass
        self._traces.clear()
        self._draft_vars.clear()
        self._dependent_controls.clear()
        self.nudge_buttons = []
        self.text_entry = self.size_entry = self.position_menu = self.position_label = None
        self.enable_button = self.preview_button = self.apply_button = self._cancel_button = None
        if close_preview:
            preview = self.app.preview_window_handler
            preview.close()
            preview.last_watermark_data = {}
            preview.last_origin = None

    def _on_destroy(self, event):
        """Handle direct widget destruction as well as explicit dialog closes."""
        if event.widget is self.window:
            self._cleanup()
            self.window = None

    def close(self, close_preview: bool = True) -> None:
        """Discard the draft and close its preview, except during translation."""
        self._cleanup(close_preview)
        window, self.window = self.window, None
        try:
            if window is not None and window.winfo_exists():
                window.destroy()
        except TclError:
            pass

    def open(self, draft=None):
        """Open the watermark dialog, restoring uncommitted values if supplied."""
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_set()
            return
        draft = draft or {}
        self.window = Toplevel(self.parent)
        self._draft_active = True
        dialog_window = self.window
        dialog_window.bind("<Destroy>", self._on_destroy, add="+")
        dialog_window.bind("<Escape>", lambda event: self.close())
        dialog_window.title(get_ui_string(self.app.strings, "wm_settings"))
        dialog_window.columnconfigure(1, weight=1, minsize=260)
        self.app.apply_theme_to_window(dialog_window)
        dialog_window.focus_set()

        settings = self.app.app_settings.snapshot()
        temp_enabled = IntVar(dialog_window, value=1 if settings["watermark_enabled"] == "True" else 0)
        temp_text = StringVar(dialog_window, value=settings["watermark_text"])
        temp_color = StringVar(dialog_window, value=settings["watermark_color"])
        temp_size = StringVar(dialog_window, value=str(settings["watermark_size"]))
        temp_position = StringVar(dialog_window, value=settings["watermark_position"])
        temp_x_ratio = DoubleVar(dialog_window, value=settings["watermark_x_ratio"])
        temp_y_ratio = DoubleVar(dialog_window, value=settings["watermark_y_ratio"])

        self._draft_vars = dict(enabled=temp_enabled, text=temp_text, color=temp_color, size=temp_size,
                                position=temp_position, x_ratio=temp_x_ratio, y_ratio=temp_y_ratio)
        for key, value in draft.items():
            self._draft_vars[key].set(value)

        ttk.Label(dialog_window, text=get_ui_string(self.app.strings, "wm_enable")).grid(row=0, column=0, sticky="W", padx=10, pady=5)
        chk = ttk.Checkbutton(dialog_window, variable=temp_enabled)
        chk.grid(row=0, column=1, sticky="W", padx=10, pady=5)
        self.enable_button = chk

        ttk.Label(dialog_window, text=get_ui_string(self.app.strings, "wm_text")).grid(row=1, column=0, sticky="W", padx=10, pady=5)
        entry_text = ttk.Entry(dialog_window, textvariable=temp_text, width=30)
        entry_text.grid(row=1, column=1, padx=10, pady=5, sticky="EW")
        self.text_entry = entry_text

        ttk.Label(dialog_window, text=get_ui_string(self.app.strings, "wm_color")).grid(row=2, column=0, sticky="W", padx=10, pady=5)
        current_color_frame = ttk.Frame(dialog_window)
        current_color_frame.grid(row=2, column=1, padx=10, pady=5, sticky="W")

        ttk.Label(dialog_window, text=get_ui_string(self.app.strings, "wm_color_select")).grid(row=3, column=0, sticky="W", padx=10, pady=5)
        color_choice_frame = ttk.Frame(dialog_window)
        color_choice_frame.grid(row=3, column=1, padx=10, pady=5, sticky="W")

        preset_colors = ["#FFA500", "#FF0000", "#00FF00", "#0000FF"]
        preselect_buttons: Dict[str, tkButton] = {}

        current_color_swatch = tkButton(current_color_frame, width=3, height=1, relief="flat")
        current_color_swatch._hsph_swatch_color = validated_highlight_color(temp_color.get(), "#FFA500")
        current_color_swatch._hsph_swatch_selected = False
        current_color_swatch.pack(side="left")

        def update_color_swatches():
            selected_color = validated_highlight_color(temp_color.get(), "#FFA500")
            current_color_swatch._hsph_swatch_color = selected_color
            current_color_swatch.config(background=selected_color, activebackground=selected_color)
            for col, btn in preselect_buttons.items():
                is_selected = col == selected_color
                btn._hsph_swatch_selected = is_selected
                btn.config(relief="sunken" if is_selected else "flat")

        def on_color_select(color):
            if self.window is dialog_window and self._editable():
                temp_color.set(color)

        for col in preset_colors:
            btn = tkButton(color_choice_frame, bg=col, width=3, height=1, relief="flat", command=lambda c=col: on_color_select(c))
            btn._hsph_swatch_color = col
            btn._hsph_swatch_selected = False
            btn.pack(side="left", padx=2)
            preselect_buttons[col] = btn

        def choose_custom_color():
            if self.window is not dialog_window or not self._editable():
                return
            try:
                _, selected_hex = colorchooser.askcolor(
                    color=validated_highlight_color(temp_color.get(), "#FFA500"), parent=dialog_window
                )
            except TclError:
                if self.window is dialog_window and self._draft_active:
                    raise
                return  # The parent may have closed during the native dialog's event loop.
            if selected_hex and self.window is dialog_window and self._editable():
                temp_color.set(selected_hex.upper())

        btn_custom_color = ttk.Button(
            color_choice_frame,
            text=get_ui_string(self.app.strings, "wm_custom_color"),
            command=choose_custom_color,
        )
        btn_custom_color.pack(side="left", padx=(8, 0))

        def on_color_change(*args):
            update_color_swatches()

        self._trace(temp_color, on_color_change)
        update_color_swatches()

        ttk.Label(dialog_window, text=get_ui_string(self.app.strings, "wm_size")).grid(row=4, column=0, sticky="W", padx=10, pady=5)
        entry_size = ttk.Spinbox(dialog_window, from_=1, to=100, textvariable=temp_size, width=5)
        entry_size.grid(row=4, column=1, padx=10, pady=5, sticky="W")
        self.size_entry = entry_size

        ttk.Label(dialog_window, text=get_ui_string(self.app.strings, "wm_pos")).grid(row=5, column=0, sticky="W", padx=10, pady=5)
        position_labels = {
            key: get_ui_string(self.app.strings, "wm_pos_" + key) for key in ("top", "bottom", "custom")
        }
        self.position_label = StringVar(dialog_window, value=position_labels[temp_position.get()])
        option_position = ttk.Menubutton(dialog_window, textvariable=self.position_label)
        position_menu = Menu(option_position, tearoff=False)
        option_position.configure(menu=position_menu)

        def select_position(position):
            if self.window is dialog_window and self._editable():
                temp_position.set(position)

        for position, label in position_labels.items():
            position_menu.add_command(label=label, command=partial(select_position, position))
        self.position_menu = position_menu
        option_position.grid(row=5, column=1, padx=10, pady=5, sticky="W")

        ttk.Label(dialog_window, text=get_ui_string(self.app.strings, "wm_adjust")).grid(row=6, column=0, sticky="W", padx=10, pady=5)
        adjust_frame = ttk.Frame(dialog_window)
        adjust_frame.grid(row=6, column=1, padx=10, pady=5, sticky="W")

        def set_preset_position():
            if temp_position.get() in {"top", "bottom"}:
                x_ratio, y_ratio = get_position_ratios(temp_position.get())
                temp_x_ratio.set(x_ratio)
                temp_y_ratio.set(y_ratio)

        def nudge_position(dx: float, dy: float):
            if self.window is not dialog_window or not self._editable():
                return
            x_ratio, y_ratio = get_position_ratios(temp_position.get(), temp_x_ratio.get(), temp_y_ratio.get())
            temp_position.set("custom")
            temp_x_ratio.set(max(0.0, min(1.0, x_ratio + dx)))
            temp_y_ratio.set(max(0.0, min(1.0, y_ratio + dy)))

        fine_nudge_step = 0.0025
        coarse_nudge_step = 0.01
        repeat_after_id = None

        def stop_repeating_nudge(*args):
            nonlocal repeat_after_id
            if repeat_after_id is not None:
                try:
                    dialog_window.after_cancel(repeat_after_id)
                except TclError:
                    pass
                repeat_after_id = None

        def repeat_nudge(dx: float, dy: float):
            nonlocal repeat_after_id
            repeat_after_id = None
            if self.window is not dialog_window or not self._editable():
                return
            nudge_position(dx, dy)
            repeat_after_id = dialog_window.after(60, repeat_nudge, dx, dy)

        def start_repeating_nudge(dx: float, dy: float, *args):
            nonlocal repeat_after_id
            stop_repeating_nudge()
            if self.window is not dialog_window or not self._editable():
                return
            nudge_position(dx, dy)
            repeat_after_id = dialog_window.after(300, repeat_nudge, dx, dy)

        def get_nudge_delta(direction_x: int, direction_y: int, coarse: bool = False) -> tuple[float, float]:
            step = coarse_nudge_step if coarse else fine_nudge_step
            return direction_x * step, direction_y * step

        def is_shift_pressed(event) -> bool:
            return bool(event.state & 0x0001)

        def start_mouse_nudge(direction_x: int, direction_y: int, event):
            dx, dy = get_nudge_delta(direction_x, direction_y, coarse=is_shift_pressed(event))
            start_repeating_nudge(dx, dy)

        def make_nudge_button(text: str, direction_x: int, direction_y: int):
            button = ttk.Button(adjust_frame, text=text, width=3)
            button.bind("<ButtonPress-1>", partial(start_mouse_nudge, direction_x, direction_y))
            button.bind("<ButtonRelease-1>", stop_repeating_nudge)
            button.bind("<Leave>", stop_repeating_nudge)
            button.pack(side="left", padx=1)
            return button

        self.nudge_buttons = [make_nudge_button(label, dx, dy) for label, dx, dy in (
            ("←", -1, 0), ("↑", 0, -1), ("↓", 0, 1), ("→", 1, 0),
        )]

        keyboard_nudge_bindings = {
            "<Left>": ("<KeyRelease-Left>", -1, 0, False),
            "<Right>": ("<KeyRelease-Right>", 1, 0, False),
            "<Up>": ("<KeyRelease-Up>", 0, -1, False),
            "<Down>": ("<KeyRelease-Down>", 0, 1, False),
            "<Shift-Left>": ("<KeyRelease-Left>", -1, 0, True),
            "<Shift-Right>": ("<KeyRelease-Right>", 1, 0, True),
            "<Shift-Up>": ("<KeyRelease-Up>", 0, -1, True),
            "<Shift-Down>": ("<KeyRelease-Down>", 0, 1, True),
        }

        def start_keyboard_nudge(direction_x: int, direction_y: int, coarse: bool, event):
            if event.widget in (entry_text, entry_size):
                return
            dx, dy = get_nudge_delta(direction_x, direction_y, coarse=coarse)
            start_repeating_nudge(dx, dy)
            return "break"

        for sequence, (release_sequence, direction_x, direction_y, coarse) in keyboard_nudge_bindings.items():
            dialog_window.bind(sequence, partial(start_keyboard_nudge, direction_x, direction_y, coarse))
            dialog_window.bind(release_sequence, stop_repeating_nudge)
        dialog_window.bind("<FocusOut>", stop_repeating_nudge, add="+")

        def preview(force_open=True):
            if (self.window is not dialog_window or not self._draft_active or self._busy
                    or getattr(self.app, "output_options_locked", False)):
                return
            if force_open and not self._editable():
                return
            values = self._validated_draft(report_error=force_open) if temp_enabled.get() else {
                "watermark_color": "#FFA500", "watermark_size": 16,
            }
            if values is None:
                return
            self.app.preview_watermark(
                temp_enabled.get(),
                temp_text.get(),
                values["watermark_color"],
                values["watermark_size"],
                temp_position.get(),
                temp_x_ratio.get(),
                temp_y_ratio.get(),
                self.app.current_preview_page,
                origin=dialog_window,
                force_open=force_open,
            )

        def update_preview(*args):
            preview(force_open=False)

        def on_enabled_change(*args):
            self._update_controls()
            update_preview()

        self._trace(temp_enabled, on_enabled_change)
        self._trace(temp_text, update_preview)
        self._trace(temp_color, update_preview)
        self._trace(temp_size, update_preview)

        def on_position_change(*args):
            self.position_label.set(position_labels[temp_position.get()])
            set_preset_position()
            update_preview()

        self._trace(temp_position, on_position_change)
        self._trace(temp_x_ratio, update_preview)
        self._trace(temp_y_ratio, update_preview)

        btn_preview = ttk.Button(dialog_window, text=get_ui_string(self.app.strings, "btn_preview"), command=lambda: preview(force_open=True))
        btn_preview.grid(row=7, column=0, columnspan=2, pady=10)

        btn_apply = ttk.Button(dialog_window, text=get_ui_string(self.app.strings, "btn_apply"), command=self.apply_changes)
        btn_apply.grid(row=8, column=0, pady=10, padx=10, sticky="E")

        def cancel_changes():
            stop_repeating_nudge()
            self.close(close_preview=True)

        dialog_window.protocol("WM_DELETE_WINDOW", cancel_changes)

        btn_cancel = ttk.Button(
            dialog_window,
            text=get_ui_string(self.app.strings, "btn_cancel"),
            command=cancel_changes,
        )
        btn_cancel.grid(row=8, column=1, pady=10, padx=10, sticky="W")
        self._cancel_button = btn_cancel
        self._stop_nudge = stop_repeating_nudge
        self._dependent_controls = [
            entry_text, current_color_swatch, *preselect_buttons.values(), btn_custom_color,
            entry_size, option_position, *self.nudge_buttons, btn_preview,
        ]
        self.preview_button = btn_preview
        self.apply_button = btn_apply
        self.set_busy(getattr(self.app, "output_options_locked", False))
        self.app.apply_theme_to_window(dialog_window)
        self._update_controls()

    def apply_changes(self):
        """Publish the local draft only when no operation owns the output options."""
        if not self._draft_active or self._busy or getattr(self.app, "output_options_locked", False):
            return
        self._stop_nudge()
        values = self._validated_draft(report_error=True)
        if values is None:
            return
        self.app.app_settings.update_settings(values)
        self.close()

    def _validated_draft(self, report_error=False):
        """Validate without mutating raw edits or publishing partial settings."""
        draft = self._draft()
        color = validated_highlight_color(draft["color"], "")
        try:
            size = int(draft["size"]) if draft["size"].isascii() and draft["size"].isdigit() else 0
        except ValueError:
            size = 0
        error = "wm_invalid_color" if not color else "wm_invalid_size" if not 1 <= size <= 100 else None
        if error:
            if report_error:
                show_error(self.app, get_ui_string(self.app.strings, "error"), get_ui_string(self.app.strings, error))
            return None
        return {
            "watermark_enabled": "True" if draft["enabled"] else "False",
            "watermark_text": draft["text"],
            "watermark_color": color,
            "watermark_size": size,
            "watermark_position": draft["position"],
            "watermark_x_ratio": draft["x_ratio"],
            "watermark_y_ratio": draft["y_ratio"],
        }

    def refresh_ui_strings(self):
        """Rebuild translated controls while retaining the raw, uncommitted draft."""
        try:
            if self.window and self.window.winfo_exists():
                draft = self._draft()
                self.close(close_preview=False)
                self.open(draft)
                self.app.preview_window_handler.last_origin = self.window
        except Exception as exc:
            logging.getLogger("dialogs").exception("Unexpected error refreshing watermark dialog: %s", exc)
