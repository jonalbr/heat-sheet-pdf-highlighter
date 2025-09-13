"""
Dialog windows (Filter and Watermark dialogs)
"""

import csv
import re
import time
from tkinter import WORD, IntVar, Label, StringVar, Text, Toplevel, filedialog, messagebox, ttk
from tkinter import Button as tkButton
from typing import TYPE_CHECKING, Dict, Optional
import logging

if TYPE_CHECKING:
    from src.gui.main_window import PDFHighlighterApp

from ..models import HighlightMode
from ..version import Version
from .ui_strings import get_ui_string
from .widgets import Tooltip


class UpdateDialogs:
    """Handles all GUI interactions for the updater functionality."""

    def __init__(self, app_instance: "PDFHighlighterApp"):
        self.app = app_instance
        # Internal counters to avoid reading Tk widgets from worker threads
        self._dl_total_size: int = 0
        self._dl_downloaded_bytes: int = 0

    # --- helper to marshal work to Tk main thread ---
    def _ui(self, fn):
        try:
            self.app.root.after(0, fn)
        except Exception as e:
            logging.getLogger("dialogs").exception("Error scheduling UI update: %s", e)

    def show_up_to_date(self):
        """Show message that app is up to date."""
        messagebox.showinfo(
            get_ui_string(self.app.strings, "upd_ok"),
            get_ui_string(self.app.strings, "upd_latest"),
            parent=self.app.root,
        )

    def show_update_available(self, latest_version: Version) -> Optional[bool]:
        """
        Show update available dialog.

        Returns:
            True if user wants to update
            False if user doesn't want to update
            None if user cancelled
        """
        return messagebox.askyesnocancel(
            get_ui_string(self.app.strings, "upd_avail"),
            get_ui_string(self.app.strings, "upd_prompt").format(latest_version),
            icon="question",
            default="yes",
            parent=self.app.root,
        )

    def show_update_reminder_choice(self) -> bool:
        """
        Show dialog asking if user wants to be reminded again.

        Returns:
            True if user doesn't want to be reminded again
            False if user wants to be reminded again
        """
        return messagebox.askokcancel(
            get_ui_string(self.app.strings, "upd_info"),
            get_ui_string(
                self.app.strings,
                "upd_note",
            ),
            parent=self.app.root,
        )

    def show_update_error_retry(self, error_message: str) -> bool:
        """
        Show update error dialog with retry option.

        Returns:
            True if user wants to retry
            False if user doesn't want to retry
        """
        return messagebox.askretrycancel(
            get_ui_string(self.app.strings, "upd_error"),
            get_ui_string(self.app.strings, "upd_check_failed").format(error_message),
            parent=self.app.root,
        )

    def show_download_error(self, error_message: str):
        """Show download error message."""
        messagebox.showerror(
            get_ui_string(self.app.strings, "error"),
            get_ui_string(self.app.strings, "upd_download_failed").format(error_message),
            parent=self.app.root,
        )

    def setup_download_progress(self, total_size: int):
        """Setup progress bar for download and show initial label."""
        self._dl_total_size = int(total_size)
        self._dl_downloaded_bytes = 0
        if hasattr(self.app, "progress_bar"):
            def _init_progress_ui():
                try:
                    self.app.progress_bar["maximum"] = self._dl_total_size
                    self._reset_progressbar_value()
                    # Show initial label for download
                    if hasattr(self.app, "status_var"):
                        self.app.status_var.set(get_ui_string(self.app.strings, "upd_progress").format(0, self._dl_total_size / (1024 * 1024), 0))
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

            text = get_ui_string(self.app.strings, "upd_progress").format(downloaded_MB, total_MB, remaining_time)
            self._ui(lambda: self.app.status_var.set(text))

    def close_application(self):
        """Close the application."""
        if hasattr(self.app, "root"):
            self._ui(lambda: self.app.root.destroy())

    def get_progress_value(self) -> float:
        """Get current progress bar value."""
        return float(self._dl_downloaded_bytes)

    def start_download_ui(self):
        """Start download UI state."""
        self._ui(self.app.start_download)

    def is_download_cancelled(self) -> bool:
        """Check if download was cancelled by user."""
        return self.app.is_download_aborted()

    def finish_download_ui(self):
        """Finish download UI state."""
        self._ui(self.app.finish_download)


class FilterDialog:
    """Filter configuration dialog."""

    def __init__(self, app_instance: "PDFHighlighterApp"):
        self.app = app_instance
        self.parent = self.app.root
        self.window = None

    def open(self):
        """Open the filter dialog window."""
        self.window = Toplevel(self.parent)
        self.window.title(get_ui_string(self.app.strings, "btn_filter"))
        self.window.grab_set()
        self.window.focus_set()

        def apply_changes():
            self.app.names_var.set(entry_names.get("1.0", "end-1c"))
            self.app.highlight_mode_var.set(temp_highlight_mode_var.get())
            self.app.enable_filter_var.set(self.app.enable_filter_var.get())
            if self.window:
                self.window.destroy()

        def clear_text(*args):
            entry_names.delete("1.0", "end")

        def import_names(*args):
            filename = filedialog.askopenfilename(
                parent=self.window,
                filetypes=[
                    (get_ui_string(self.app.strings, "file_filter_csv"), "*.csv;*.txt"),
                    (get_ui_string(self.app.strings, "file_filter_all"), "*.*"),
                ],
            )
            if filename:
                try:
                    with open(filename, "r", encoding="utf-8") as file:
                        if filename.endswith(".csv"):
                            reader = csv.reader(file)
                            names = next(reader)
                        else:
                            content = file.read()
                            names = [name.strip() for name in re.split(r"[\n,]+", content)]
                    entry_names.delete("1.0", "end")
                    entry_names.insert("1.0", ", ".join(names))
                except Exception as e:
                    logging.getLogger("dialogs").exception("Failed to import names from %s: %s", filename, e)

        def insert_comma(*args):
            text = entry_names.get("1.0", "end-1c")
            if not re.search(r",\s*$", text):
                entry_names.insert("end", ", ")
            return "break"

        checkbox_filter = ttk.Checkbutton(self.window, text=get_ui_string(self.app.strings, "flt_enable"), variable=self.app.enable_filter_var)
        checkbox_filter.grid(row=0, column=0, columnspan=2, sticky="W", padx=10, pady=5)
        Tooltip(checkbox_filter, text=get_ui_string(self.app.strings, "flt_info"))

        temp_highlight_mode_var = StringVar(value=self.app.highlight_mode_var.get())
        label_names = ttk.Label(self.window, text=get_ui_string(self.app.strings, "flt_names"))
        label_names.grid(row=1, column=0, sticky="W", padx=10)

        entry_names = Text(self.window, height=6, width=50, wrap=WORD)
        entry_names.insert(1.0, self.app.names_var.get())
        entry_names.grid(row=1, column=1, sticky="WE", padx=10)
        entry_names.bind("<Return>", insert_comma)

        button_frame = ttk.Frame(self.window)
        button_frame.grid(row=2, column=1, sticky="W", padx=10, pady=10)

        button_clear = ttk.Button(button_frame, text=get_ui_string(self.app.strings, "btn_clear"), command=clear_text)
        button_clear.grid(row=0, column=0, sticky="W", padx=10)

        button_import = ttk.Button(button_frame, text=get_ui_string(self.app.strings, "btn_import"), command=import_names)
        button_import.grid(row=0, column=1, sticky="W", padx=10)

        label_highlight_mode = ttk.Label(self.window, text=get_ui_string(self.app.strings, "flt_mode"))
        label_highlight_mode.grid(row=3, column=0, sticky="W", padx=10)

        radio_highlight_only = ttk.Radiobutton(
            self.window,
            text=get_ui_string(self.app.strings, "flt_mode_blue"),
            variable=temp_highlight_mode_var,
            value=HighlightMode.ONLY_NAMES.name,
        )
        radio_highlight_only.grid(row=3, column=1, sticky="W", padx=10)

        radio_highlight_color = ttk.Radiobutton(
            self.window,
            text=get_ui_string(self.app.strings, "flt_mode_blue_yellow"),
            variable=temp_highlight_mode_var,
            value=HighlightMode.NAMES_DIFF_COLOR.name,
        )
        radio_highlight_color.grid(row=4, column=1, sticky="W", padx=10)

        button_frame2 = ttk.Frame(self.window)
        button_frame2.grid(row=5, column=0, columnspan=2, sticky="WE", padx=10, pady=10)

        button_apply = ttk.Button(button_frame2, text=self.app.strings["btn_apply"], command=apply_changes)
        button_apply.pack(side="left", padx=10, expand=True)

        button_abort = ttk.Button(button_frame2, text=self.app.strings["btn_cancel"], command=lambda: self.window.destroy() if self.window else None)
        button_abort.pack(side="right", padx=10, expand=True)

    def refresh_ui_strings(self):
        """Refresh the Filter dialog UI strings by recreating the window if open."""
        try:
            if self.window and self.window.winfo_exists():
                try:
                    self.window.destroy()
                except Exception as e:
                    logging.getLogger("dialogs").exception("Error destroying filter dialog: %s", e)
                # Re-open will recreate the dialog using current translations
                self.open()
        except Exception as e:
            logging.getLogger("dialogs").exception("Unexpected error refreshing filter dialog: %s", e)


class WatermarkDialog:
    """Watermark configuration dialog."""

    def __init__(self, app_instance: "PDFHighlighterApp"):
        self.app = app_instance
        self.parent = self.app.root
        self.window = None

    def open(self):
        """Open the watermark dialog window."""
        self.window = Toplevel(self.parent)
        self.window.title(get_ui_string(self.app.strings, "wm_settings"))
        self.window.focus_set()

        temp_enabled = IntVar(value=1 if self.app.app_settings.settings.get("watermark_enabled") == "True" else 0)
        temp_text = StringVar(value=self.app.app_settings.settings.get("watermark_text"))
        temp_color = StringVar(value=self.app.app_settings.settings.get("watermark_color"))
        temp_size = StringVar(value=str(self.app.app_settings.settings.get("watermark_size")))
        temp_position = StringVar(value=self.app.app_settings.settings.get("watermark_position"))

        Label(self.window, text=get_ui_string(self.app.strings, "wm_enable")).grid(row=0, column=0, sticky="W", padx=10, pady=5)
        chk = ttk.Checkbutton(self.window, variable=temp_enabled)
        chk.grid(row=0, column=1, sticky="W", padx=10, pady=5)

        Label(self.window, text=get_ui_string(self.app.strings, "wm_text")).grid(row=1, column=0, sticky="W", padx=10, pady=5)
        entry_text = ttk.Entry(self.window, textvariable=temp_text)
        entry_text.grid(row=1, column=1, padx=10, pady=5)

        Label(self.window, text=get_ui_string(self.app.strings, "wm_color_hex")).grid(row=2, column=0, sticky="W", padx=10, pady=5)
        entry_color = ttk.Entry(self.window, textvariable=temp_color)
        entry_color.grid(row=2, column=1, padx=10, pady=5)

        # Preselect color frame
        preselect_frame = ttk.Frame(self.window)
        preselect_frame.grid(row=3, column=0, columnspan=2, padx=10, pady=5, sticky="W")
        Label(preselect_frame, text=get_ui_string(self.app.strings, "wm_pre_color")).pack(side="left")

        preset_colors = ["#FFA500", "#FF0000", "#00FF00", "#0000FF"]
        preselect_buttons: Dict[str, tkButton] = {}

        def on_color_select(color):
            temp_color.set(color)
            for col, btn in preselect_buttons.items():
                btn.config(relief="flat" if col != color else "sunken")

        for col in preset_colors:
            btn = tkButton(preselect_frame, bg=col, width=3, height=1, relief="flat", command=lambda c=col: on_color_select(c))
            btn.pack(side="left", padx=2)
            preselect_buttons[col] = btn

        def on_color_entry(*args):
            for btn in preselect_buttons.values():
                btn.config(relief="flat")

        temp_color.trace_add("write", on_color_entry)

        Label(self.window, text=get_ui_string(self.app.strings, "wm_size")).grid(row=4, column=0, sticky="W", padx=10, pady=5)
        entry_size = ttk.Spinbox(self.window, from_=1, to=100, textvariable=temp_size, width=5)
        entry_size.grid(row=4, column=1, padx=10, pady=5, sticky="W")

        Label(self.window, text=get_ui_string(self.app.strings, "wm_pos")).grid(row=5, column=0, sticky="W", padx=10, pady=5)
        position_options = ["top", "bottom"]
        option_position = ttk.OptionMenu(self.window, temp_position, temp_position.get(), *position_options)
        option_position.grid(row=5, column=1, padx=10, pady=5, sticky="W")

        def preview(force_open=True):
            self.app.preview_watermark(
                temp_enabled.get(),
                temp_text.get(),
                temp_color.get(),
                int(temp_size.get()) if temp_size.get().isdigit() else 16,
                temp_position.get(),
                self.app.current_preview_page,
                origin=self.window,
                force_open=force_open,
            )

        def update_preview(*args):
            preview(force_open=False)

        temp_enabled.trace_add("write", update_preview)
        temp_text.trace_add("write", update_preview)
        temp_color.trace_add("write", update_preview)
        temp_size.trace_add("write", update_preview)
        temp_position.trace_add("write", update_preview)

        btn_preview = ttk.Button(self.window, text=get_ui_string(self.app.strings, "btn_preview"), command=lambda: preview(force_open=True))
        btn_preview.grid(row=7, column=0, columnspan=2, pady=10)

        def apply_changes():
            self.app.app_settings.update_setting("watermark_enabled", "True" if temp_enabled.get() else "False")
            self.app.app_settings.update_setting("watermark_text", temp_text.get())
            self.app.app_settings.update_setting("watermark_color", temp_color.get())
            self.app.app_settings.update_setting("watermark_size", int(temp_size.get()) if temp_size.get().isdigit() else 16)
            self.app.app_settings.update_setting("watermark_position", temp_position.get())
            if self.window:
                self.window.destroy()

        btn_apply = ttk.Button(self.window, text=get_ui_string(self.app.strings, "btn_apply"), command=apply_changes)
        btn_apply.grid(row=8, column=0, pady=10, padx=10, sticky="E")

        btn_cancel = ttk.Button(
            self.window, text=get_ui_string(self.app.strings, "btn_cancel"), command=lambda: self.window.destroy() if self.window else None
        )
        btn_cancel.grid(row=8, column=1, pady=10, padx=10, sticky="W")

    def refresh_ui_strings(self):
        """Refresh the Watermark dialog UI strings by recreating the window if open."""
        try:
            if self.window and self.window.winfo_exists():
                try:
                    self.window.destroy()
                except Exception as e:
                    logging.getLogger("dialogs").exception("Error destroying watermark dialog: %s", e)
                # Re-open will recreate the dialog using current translations
                self.open()
        except Exception as e:
            logging.getLogger("dialogs").exception("Unexpected error refreshing watermark dialog: %s", e)
