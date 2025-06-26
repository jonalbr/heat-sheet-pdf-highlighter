"""
Dialog windows (Filter and Watermark dialogs)
"""
from typing import TYPE_CHECKING, Dict, Optional
import csv
import re
import time
from tkinter import (
    StringVar, IntVar, Text, ttk, Toplevel, Label, filedialog,
    WORD, messagebox
)

from tkinter import Button as tkButton

if TYPE_CHECKING:
    from src.gui.main_window import PDFHighlighterApp

from ..models import HighlightMode
from ..version import Version
from .widgets import Tooltip


class UpdateDialogs:
    """Handles all GUI interactions for the updater functionality."""
    
    def __init__(self, app_instance: "PDFHighlighterApp"):
        self.app = app_instance
        
    def show_up_to_date(self):
        """Show message that app is up to date."""
        messagebox.showinfo(
            self.app.strings["Up to Date"],
            self.app.strings["You are already using the latest version."],
            parent=self.app.root
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
            self.app.strings["Update Available"],
            self.app.strings["A new version ({0}) is available. Do you want to update?"].format(latest_version),
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
            self.app.strings["Update Information"],
            self.app.strings["Click 'yes' to not be asked again for this update. You can still check manually for updates. If there is a newer version available, you will be asked again."],
            parent=self.app.root
        )
    
    def show_update_error_retry(self, error_message: str) -> bool:
        """
        Show update error dialog with retry option.
        
        Returns:
            True if user wants to retry
            False if user doesn't want to retry
        """
        return messagebox.askretrycancel(
            self.app.strings["Update Error"], 
            self.app.strings["Failed to check for updates: {0}"].format(error_message),
            parent=self.app.root
        )
    
    def show_download_error(self, error_message: str):
        """Show download error message."""
        messagebox.showerror(
            self.app.strings["Error"], 
            self.app.strings["Failed to download the installer: {0}"].format(error_message),
            parent=self.app.root
        )
    
    def setup_download_progress(self, total_size: int):
        """Setup progress bar for download."""
        if hasattr(self.app, 'progress_bar'):
            self.app.progress_bar["maximum"] = total_size
    
    def update_download_progress(self, data_size: int):
        """Update download progress bar."""
        if hasattr(self.app, 'progress_bar'):
            self.app.progress_bar["value"] += data_size
    
    def update_download_status(self, start_time: float, total_size: int):
        """Update download status text."""
        elapsed_time = time.time() - start_time
        current_value = self.app.progress_bar["value"] if hasattr(self.app, 'progress_bar') else 0
        
        # Update status text only if status_var exists
        if hasattr(self.app, 'status_var') and elapsed_time > 0 and current_value > 0:
            speed = current_value / elapsed_time
            remaining_time = (total_size - current_value) / speed if speed > 0 else 0
            downloaded_MB = current_value / (1024 * 1024)
            total_MB = total_size / (1024 * 1024)
            
            self.app.status_var.set(
                self.app.strings["Downloading... {0:.1f} MB of {1:.1f} MB, {2:.0f} seconds remaining"].format(
                    downloaded_MB, total_MB, remaining_time
                )
            )
        
        self.app.root.update()
    
    def update_gui(self):
        """Update the GUI."""
        if hasattr(self.app, 'root'):
            self.app.root.update()
    
    def close_application(self):
        """Close the application."""
        if hasattr(self.app, 'root'):
            self.app.root.destroy()
    
    def get_progress_value(self) -> float:
        """Get current progress bar value."""
        if hasattr(self.app, 'progress_bar'):
            return self.app.progress_bar["value"]
        return 0.0
    
    def start_download_ui(self):
        """Start download UI state."""
        self.app.start_download()
    
    def is_download_cancelled(self) -> bool:
        """Check if download was cancelled by user."""
        return self.app.is_download_aborted()
    
    def finish_download_ui(self):
        """Finish download UI state."""
        self.app.finish_download()


class FilterDialog:
    """Filter configuration dialog."""
    
    def __init__(self, app_instance: "PDFHighlighterApp"):
        self.app = app_instance
        self.parent = self.app.root
        self.window = None
        
    def open(self):
        """Open the filter dialog window."""
        self.window = Toplevel(self.parent)
        self.window.title(self.app.strings["filter"])
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
                parent=self.window, filetypes=[(self.app.strings["CSV and Text files"], "*.csv;*.txt"), (self.app.strings["All files"], "*.*")]
            )
            if filename:
                with open(filename, "r", encoding="utf-8") as file:
                    if filename.endswith(".csv"):
                        reader = csv.reader(file)
                        names = next(reader)
                    else:
                        content = file.read()
                        names = [name.strip() for name in re.split(r"[\n,]+", content)]
                entry_names.delete("1.0", "end")
                entry_names.insert("1.0", ", ".join(names))

        def insert_comma(*args):
            text = entry_names.get("1.0", "end-1c")
            if not re.search(r",\s*$", text):
                entry_names.insert("end", ", ")
            return "break"

        checkbox_filter = ttk.Checkbutton(self.window, text=self.app.strings["Enable Filter"], variable=self.app.enable_filter_var)
        checkbox_filter.grid(row=0, column=0, columnspan=2, sticky="W", padx=10, pady=5)
        Tooltip(checkbox_filter, text=self.app.strings["Enable highlighting lines with specific names."])

        temp_highlight_mode_var = StringVar(value=self.app.highlight_mode_var.get())
        label_names = ttk.Label(self.window, text=self.app.strings["Names"])
        label_names.grid(row=1, column=0, sticky="W", padx=10)

        entry_names = Text(self.window, height=6, width=50, wrap=WORD)
        entry_names.insert(1.0, self.app.names_var.get())
        entry_names.grid(row=1, column=1, sticky="WE", padx=10)
        entry_names.bind("<Return>", insert_comma)

        button_frame = ttk.Frame(self.window)
        button_frame.grid(row=2, column=1, sticky="W", padx=10, pady=10)

        button_clear = ttk.Button(button_frame, text=self.app.strings["Clear"], command=clear_text)
        button_clear.grid(row=0, column=0, sticky="W", padx=10)

        button_import = ttk.Button(button_frame, text=self.app.strings["Import"], command=import_names)
        button_import.grid(row=0, column=1, sticky="W", padx=10)

        label_highlight_mode = ttk.Label(self.window, text=self.app.strings["Highlight Mode"])
        label_highlight_mode.grid(row=3, column=0, sticky="W", padx=10)

        radio_highlight_only = ttk.Radiobutton(
            self.window,
            text=self.app.strings["Highlight lines with matched names in blue, others are not highlighted"],
            variable=temp_highlight_mode_var,
            value=HighlightMode.ONLY_NAMES.name,
        )
        radio_highlight_only.grid(row=3, column=1, sticky="W", padx=10)

        radio_highlight_color = ttk.Radiobutton(
            self.window,
            text=self.app.strings["Highlight lines with matched names in blue, others in yellow"],
            variable=temp_highlight_mode_var,
            value=HighlightMode.NAMES_DIFF_COLOR.name,
        )
        radio_highlight_color.grid(row=4, column=1, sticky="W", padx=10)

        button_frame2 = ttk.Frame(self.window)
        button_frame2.grid(row=5, column=0, columnspan=2, sticky="WE", padx=10, pady=10)

        button_apply = ttk.Button(button_frame2, text=self.app.strings["Apply"], command=apply_changes)
        button_apply.pack(side="left", padx=10, expand=True)

        button_abort = ttk.Button(button_frame2, text=self.app.strings["Cancel"], command=lambda: self.window.destroy() if self.window else None)
        button_abort.pack(side="right", padx=10, expand=True)


class WatermarkDialog:
    """Watermark configuration dialog."""
    
    def __init__(self, app_instance: "PDFHighlighterApp"):
        self.app = app_instance
        self.parent = self.app.root
        self.window = None
        
    def open(self):
        """Open the watermark dialog window."""
        self.window = Toplevel(self.parent)
        self.window.title(self.app.strings["Watermark Settings"])
        self.window.focus_set()
        
        temp_enabled = IntVar(value=1 if self.app.app_settings.settings.get("watermark_enabled") == "True" else 0)
        temp_text = StringVar(value=self.app.app_settings.settings.get("watermark_text"))
        temp_color = StringVar(value=self.app.app_settings.settings.get("watermark_color"))
        temp_size = StringVar(value=str(self.app.app_settings.settings.get("watermark_size")))
        temp_position = StringVar(value=self.app.app_settings.settings.get("watermark_position"))
        
        Label(self.window, text=self.app.strings["Enable Watermark"]).grid(row=0, column=0, sticky="W", padx=10, pady=5)
        chk = ttk.Checkbutton(self.window, variable=temp_enabled)
        chk.grid(row=0, column=1, sticky="W", padx=10, pady=5)
        
        Label(self.window, text=self.app.strings["Watermark Text"]).grid(row=1, column=0, sticky="W", padx=10, pady=5)
        entry_text = ttk.Entry(self.window, textvariable=temp_text)
        entry_text.grid(row=1, column=1, padx=10, pady=5)
        
        Label(self.window, text=self.app.strings["Color (hex)"]).grid(row=2, column=0, sticky="W", padx=10, pady=5)
        entry_color = ttk.Entry(self.window, textvariable=temp_color)
        entry_color.grid(row=2, column=1, padx=10, pady=5)
        
        # Preselect color frame
        preselect_frame = ttk.Frame(self.window)
        preselect_frame.grid(row=3, column=0, columnspan=2, padx=10, pady=5, sticky="W")
        Label(preselect_frame, text=self.app.strings["Preselect Color:"]).pack(side="left")

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
        
        Label(self.window, text=self.app.strings["Size"]).grid(row=4, column=0, sticky="W", padx=10, pady=5)
        entry_size = ttk.Spinbox(self.window, from_=1, to=100, textvariable=temp_size, width=5)
        entry_size.grid(row=4, column=1, padx=10, pady=5, sticky="W")
        
        Label(self.window, text=self.app.strings["Position"]).grid(row=5, column=0, sticky="W", padx=10, pady=5)
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
        
        btn_preview = ttk.Button(self.window, text=self.app.strings["Preview"], command=lambda: preview(force_open=True))
        btn_preview.grid(row=7, column=0, columnspan=2, pady=10)

        def apply_changes():
            self.app.app_settings.update_setting("watermark_enabled", "True" if temp_enabled.get() else "False")
            self.app.app_settings.update_setting("watermark_text", temp_text.get())
            self.app.app_settings.update_setting("watermark_color", temp_color.get())
            self.app.app_settings.update_setting("watermark_size", int(temp_size.get()) if temp_size.get().isdigit() else 16)
            self.app.app_settings.update_setting("watermark_position", temp_position.get())
            if self.window:
                self.window.destroy()

        btn_apply = ttk.Button(self.window, text=self.app.strings["Apply"], command=apply_changes)
        btn_apply.grid(row=8, column=0, pady=10, padx=10, sticky="E")
        
        btn_cancel = ttk.Button(self.window, text=self.app.strings["Cancel"], command=lambda: self.window.destroy() if self.window else None)
        btn_cancel.grid(row=8, column=1, pady=10, padx=10, sticky="W")
