"""
Main application window
"""

from contextlib import suppress
from collections.abc import Callable
from dataclasses import dataclass, field, replace
import os
import logging
import queue
import subprocess
import sys
import threading
import tempfile
import time
from pathlib import Path
from tkinter.font import Font
from tkinter import Button, Canvas, IntVar, Menu, StringVar, TclError, Tk, filedialog, ttk

from PIL import Image, ImageTk
from pymupdf import Document

from ..config.paths import Paths
from ..config.settings import AppSettings
from ..constants import LANGUAGE_OPTIONS, VERSION_STR
from ..core.ocr import (
    OcrCancelled,
    create_searchable_ocr_pdf_in_process,
    document_needs_ocr,
    ensure_bundled_tessdata,
    pdf_needs_ocr,
    save_compact_pdf,
    save_pdf_path_in_process,
)
from ..core.pdf_processor import highlight_matching_data, parse_names
from ..core.watermark import watermark_pdf_page
from ..models import MarkingMode
from ..utils.localization import setup_translation
from ..utils.theme import ThemeColors, get_effective_theme, get_theme_colors, set_windows_title_bar_theme
from ..utils.updater import UpdateChecker
from ..version import Version
from .dev_tools import DevToolsWindow
from .dialogs import FilterDialog, UpdateDialogs, WatermarkDialog
from .marking_preview import MarkingPreview
from .message_dialog import ask_choice_ok_cancel, ask_ok_cancel, show_error, show_info
from .preview import PreviewWindow
from .ui_strings import _xgettext_dummy, build_strings, get_ui_string, plural_strings
from .widgets import Tooltip, scrollbar_command

THEME_ICON_SIZE = (16, 16)
UNIFORM_ACTION_BUTTON_WIDTH = 14
DETERMINATE_PROGRESS_MAXIMUM = 100
INDETERMINATE_PROGRESS_INTERVAL_MS = 15
INDETERMINATE_PROGRESS_MAXIMUM = 200
OCR_LANGUAGE_CHOICES = (
    ("ocr_language_deu", "deu"),
    ("ocr_language_eng", "eng"),
    ("ocr_language_deu_eng", "deu+eng"),
)


@dataclass(frozen=True)
class ProcessingOptions:
    search_str: str
    only_relevant: bool
    names: tuple[str, ...]
    marking_mode: MarkingMode
    highlight_color_default: str
    highlight_color_names: str
    watermark: tuple[tuple[str, str | int | float], ...] = ()
    ocr_enabled: bool = True
    ocr_language: str = "deu"
    ocr_dpi: int = 300
    ocr_reduce_large_outputs: bool = True
    needs_ocr: bool | None = None


@dataclass
class GuiRequest:
    """One cancellable worker request answered only by the Tk thread."""

    callback: Callable[[], object]
    ready: threading.Event = field(default_factory=threading.Event)
    value: object = None
    error: Exception | None = None


@dataclass
class ProcessingJob:
    """Resources and immutable input belonging to one PDF operation."""

    identifier: int
    input_file: str
    options: ProcessingOptions
    cancel: threading.Event = field(default_factory=threading.Event)
    worker: threading.Thread | None = None
    state: str = "preparing"
    result: tuple | None = None
    dialog_open: bool = False
    started: bool = False
    output_file: str = ""
    target_signature: tuple | None = None
    document: Document | None = None
    ocr_temp_file: str | None = None
    counts: tuple[int, int, int, int] = (0, 0, 0, 0)
    committed: bool = False


class PDFHighlighterApp:
    """
    A class representing a PDF Highlighter application.

    Attributes:
        root (Tk): The root Tkinter window.
    """

    def __init__(self, root: Tk):
        """
        Initializes the PDFHighlighterApp.

        Args:
            root (Tk): The root Tkinter window.
        """
        self.root = root
        self.paths = Paths()
        self.root.tk.call("lappend", "auto_path", self.paths.tcl_lib_path)

        # Initialize the settings
        self.app_settings = AppSettings(self.paths.settings_file)

        # Set up internationalization
        self._, self.n_ = setup_translation(self.app_settings.settings["language"])
        self.init_translatable_strings()

        # Initialize dialogs, preview and update components
        self.filter_dialog = FilterDialog(self)
        self.watermark_dialog = WatermarkDialog(self)
        self.preview_window_handler = PreviewWindow(self)
        self.update_dialogs = UpdateDialogs(self)
        self.update_checker = UpdateChecker(self)

        self._job = None
        self._pending_save = None
        self._result_job = None
        self._next_job_id = 0
        self._ui_messages = queue.Queue()
        self._closing = False
        self._download_cancel = threading.Event()
        self._download_id = 0
        self.processing_active = False
        self.download_active = False
        self._status = ("status_waiting", ())

        # Cached detection belongs to the currently selected source.
        self.ocr_detection_path: str | None = None
        self.ocr_detection_result: bool | None = None

        # Initialize the UI components
        self._syncing_settings = True
        self.setup_ui()
        self._syncing_settings = False

        self.marking_preview = MarkingPreview(self.root)
        self._preview_prefetch_timer = None
        self.root.after_idle(self.marking_preview.warm)
        self.root.after_idle(self._prefetch_preview)
        self.search_phrase_var.trace_add("write", self._schedule_preview_prefetch)
        self.root.after(30, self._poll_work)
        self.root.after_idle(self._show_settings_notices)

        # Check for updates AFTER UI is set up (skip when in screenshot mode to prevent race with root.destroy)
        if os.getenv("HSPH_SCREENSHOT_MODE") not in ("1", "true", "True"):
            threading.Thread(target=self.check_for_app_updates, daemon=True).start()

    def init_translatable_strings(self):
        """
        Initialize all translatable strings as instance variables.
        Call this after (re)loading gettext translations.
        """
        self.strings = build_strings(self._)

        # Plural strings - keep as raw strings for proper ngettext handling
        self.plural_strings = plural_strings

        # This call is for xgettext extraction only - it populates the .pot file
        # The actual translations are handled by get_plural_string() method
        if False:  # Never executed, only for xgettext scanning
            _xgettext_dummy(self.n_)

    def get_plural_string(self, key: str, count: int) -> str:
        """
        Get a plural string using ngettext.

        Args:
            key: The key in self.plural_strings
            count: The count to determine singular/plural

        Returns:
            Translated string
        """
        if key not in self.plural_strings:
            raise KeyError(f"Plural string key '{key}' not found")

        plural_data = self.plural_strings[key]
        return self.n_(plural_data["singular"], plural_data["plural"], count)

    def setup_ui(self):
        """
        Sets up the user interface for the PDFHighlighterApp.
        """
        self.root.title(get_ui_string(self.strings, "title"))
        width = min(760, max(1, self.root.winfo_screenwidth() - 40))
        height = min(650, max(1, self.root.winfo_screenheight() - 80))
        self.root.geometry(f"{width}x{height}")
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        # Keep named font sizes stable across theme/language changes.
        self.ui_font = Font(root=self.root, family="Arial", size=10)
        self.heading_font = Font(root=self.root, family="Arial", size=16, weight="bold")
        self.style = ttk.Style()
        self.style.configure("Horizontal.TProgressbar", thickness=20)
        self._apply_theme()
        self.theme_icon_images = self._load_theme_icon_images()

        # Add icon
        self.root.iconbitmap(self.paths.icon_path)

        self.header_frame = ttk.Frame(self.root)
        self.header_frame.grid(row=0, column=0, columnspan=3, sticky="EW")
        self.header_frame.grid_columnconfigure(1, weight=1)

        # Load and display the logo
        logo_image = Image.open(self.paths.logo_path)
        title_font = self.heading_font
        title_height = 50  # Set the desired height of the title
        logo_image = logo_image.resize((title_height, title_height))
        logo_photo = ImageTk.PhotoImage(logo_image)
        self.logo_label = ttk.Label(self.header_frame, image=logo_photo, style="Logo.TLabel")
        setattr(self.logo_label, "image", logo_photo)  # Store a reference to the image
        self.logo_label.grid(row=0, column=0, sticky="W", padx=(10, 0), pady=10)

        # Secret Dev Tools trigger: triple-click on logo
        self._dev_clicks = []  # store timestamps of clicks
        self.dev_tools = DevToolsWindow(self)

        def _on_logo_click(_event=None):
            now = time.time()
            # Keep only clicks within the last 1 second
            self._dev_clicks = [t for t in self._dev_clicks if now - t <= 1.0]
            self._dev_clicks.append(now)
            if len(self._dev_clicks) >= 3:
                self._dev_clicks.clear()
                self.dev_tools.open()

        self.logo_label.bind("<Button-1>", _on_logo_click)

        # Application title next to the logo
        self.title = ttk.Label(
            self.header_frame,
            text=get_ui_string(self.strings, "title"),
            font=title_font,
            style="Title.TLabel",
            anchor="w",
        )
        self.title.grid(row=0, column=1, sticky="EW", padx=(14, 10), pady=10)

        self.header_frame.bind("<Configure>", lambda event: self.title.configure(
            wraplength=max(80, event.width - self.header_controls_frame.winfo_reqwidth() - 110)))

        # Theme selector (system/light/dark)
        self.theme_mode_var = StringVar()
        self.theme_mode_var.set(self.app_settings.settings.get("theme_mode", "system"))
        self.theme_mode_var.trace_add("write", lambda *args: self._on_theme_mode_change(self.theme_mode_var.get()))

        self.header_controls_frame = ttk.Frame(self.header_frame)
        self.header_controls_frame.grid(row=0, column=2, sticky="E", padx=10, pady=10)

        self.theme_button = Button(
            self.header_controls_frame,
            command=self._open_theme_menu,
            width=32,
            height=30,
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            cursor="hand2",
            takefocus=True,
        )
        self.theme_menu = Menu(self.theme_button, tearoff=False)
        self.theme_menu.add_radiobutton(
            label=get_ui_string(self.strings, "theme_system"),
            variable=self.theme_mode_var,
            value="system",
        )
        self.theme_menu.add_radiobutton(
            label=get_ui_string(self.strings, "theme_light"),
            variable=self.theme_mode_var,
            value="light",
        )
        self.theme_menu.add_radiobutton(
            label=get_ui_string(self.strings, "theme_dark"),
            variable=self.theme_mode_var,
            value="dark",
        )
        self._set_theme_button_icon()
        self.theme_button.grid(row=0, column=0, sticky="E", padx=(0, 4))
        Tooltip(self.theme_button, text=get_ui_string(self.strings, "theme_menu_tooltip"))

        # Language selection
        self.language_var = StringVar()
        self.language_var.set(self.app_settings.settings["language"])
        self.language_var.trace_add("write", lambda *args: self._persist_setting("language", self.language_var.get()))

        self.language_menu = ttk.OptionMenu(
            self.header_controls_frame,
            self.language_var,
            self.app_settings.settings["language"],
            *LANGUAGE_OPTIONS,
            command=lambda _: self.on_language_change(self.language_var.get()),
        )
        self.language_menu.config(width=4)
        self.language_menu.grid(row=0, column=1, sticky="E")

        # Scroll only the main content, keeping header and version information visible.
        body = ttk.Frame(self.root)
        body.grid(row=1, column=0, columnspan=3, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.content_canvas = Canvas(body, width=1, height=1, highlightthickness=0, borderwidth=0)
        self.content_canvas.grid(row=0, column=0, sticky="nsew")
        self.content_scrollbar = ttk.Scrollbar(body, orient="vertical", command=scrollbar_command(self.content_canvas.yview))
        self.content_scrollbar.grid(row=0, column=1, sticky="ns")
        self.content_canvas.configure(yscrollcommand=self.content_scrollbar.set)
        self.content = ttk.Frame(self.content_canvas)
        self._content_item = self.content_canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.content.columnconfigure(1, weight=1)
        self.content_canvas.bind("<Configure>", self._resize_content)
        self.content.bind("<Configure>", self._content_changed)
        # Toplevel bindings apply only to this window; dialogs retain their own scrolling.
        self.root.bind("<MouseWheel>", self._scroll_content, add="+")
        self.root.bind("<FocusIn>", self._reveal_control, add="+")

        # PDF file selection
        self.label_pdf_file = ttk.Label(self.content, text=get_ui_string(self.strings, "pdf_file"))
        self.label_pdf_file.grid(row=1, column=0, sticky="E", padx=(10, 4), pady=2)
        self.pdf_file_var = StringVar()

        self.entry_file = ttk.Entry(self.content, textvariable=self.pdf_file_var, state="readonly")
        self.entry_file.grid(row=1, column=1, sticky="WE", padx=(2, 0))

        self.browse_button = ttk.Button(
            self.content,
            text=get_ui_string(self.strings, "btn_browse"),
            command=self.browse_file,
            width=-UNIFORM_ACTION_BUTTON_WIDTH,
        )
        self.browse_button.grid(row=1, column=2, sticky="E", padx=(3, 10))

        # Search string
        self.label_search_str = ttk.Label(self.content, text=get_ui_string(self.strings, "search_term"))
        self.label_search_str.grid(row=2, column=0, sticky="E", padx=(10, 4), pady=2)

        self.search_phrase_var = StringVar()
        self.search_phrase_var.set(self.app_settings.settings["search_str"])

        self.entry_search_str = ttk.Entry(self.content, textvariable=self.search_phrase_var)
        self.entry_search_str.grid(row=2, column=1, sticky="WE", padx=(2, 0))

        # Options for highlighting
        self.relevant_lines_var = IntVar()
        self.relevant_lines_var.set(self.app_settings.settings.get("mark_only_relevant_lines", 1))
        self.relevant_lines_var.trace_add(
            "write", lambda *args: self._persist_setting("mark_only_relevant_lines", self.relevant_lines_var.get())
        )

        self.checkbox_relevant_lines = ttk.Checkbutton(
            self.content, text=get_ui_string(self.strings, "mark_only_relevant"), variable=self.relevant_lines_var
        )
        self.checkbox_relevant_lines.grid(row=3, column=0, columnspan=3, sticky="W", padx=10, pady=6)

        # Filter variables
        self.names_var = StringVar()
        self.names_var.set(self.app_settings.settings.get("names", ""))
        self.names_var.trace_add("write", lambda *args: self._persist_setting("names", self.names_var.get()))

        self.marking_mode_var = StringVar(value=self.app_settings.settings["marking_mode"])
        self.marking_mode_var.trace_add(
            "write", lambda *args: self._persist_setting("marking_mode", self.marking_mode_var.get())
        )
        self.highlight_color_default_var = StringVar(value=self.app_settings.settings["highlight_color_default"])
        self.highlight_color_names_var = StringVar(value=self.app_settings.settings["highlight_color_names"])
        for key in ("highlight_color_default", "highlight_color_names"):
            variable = getattr(self, f"{key}_var")
            variable.trace_add("write", lambda *args, key=key, var=variable: self._persist_setting(key, var.get()))

        # Filter and watermark buttons
        self.options_actions = ttk.Frame(self.content)
        self.options_actions.grid(row=4, column=0, columnspan=3, sticky="ew", padx=10, pady=2)
        self.options_actions.columnconfigure(0, weight=1)
        self.button_filter = ttk.Button(
            self.options_actions,
            text=get_ui_string(self.strings, "btn_filter"),
            command=self.open_filter_window,
            width=-UNIFORM_ACTION_BUTTON_WIDTH,
        )
        self.button_filter.grid(row=0, column=0, sticky="E", padx=(0, 3), pady=2)

        self.button_watermark = ttk.Button(
            self.options_actions,
            text=get_ui_string(self.strings, "btn_watermark"),
            command=self.open_watermark_window,
            width=-UNIFORM_ACTION_BUTTON_WIDTH,
        )
        self.button_watermark.grid(row=0, column=1, sticky="E", pady=2)

        self.options_label = ttk.Label(self.content, wraplength=500, justify="left")
        self.options_label.grid(row=5, column=0, columnspan=3, sticky="ew", padx=10, pady=(8, 0))

        # Progress bar
        self.progress_bar = ttk.Progressbar(self.content, orient="horizontal", length=400, mode="determinate")
        self.progress_bar.grid(row=6, column=0, columnspan=3, padx=10, pady=12, sticky="WE")

        # Status label
        self.status_var = StringVar()
        self.status_var.set(get_ui_string(self.strings, "status_waiting"))
        self.status_label = ttk.Label(self.content, textvariable=self.status_var, wraplength=500, justify="left")
        self.status_label.grid(row=7, column=0, columnspan=3, padx=10, pady=2, sticky="EW")

        # Start/Abort button
        self.start_abort_button = ttk.Button(self.content, text=get_ui_string(self.strings, "btn_start"), command=self.start_processing)
        self.start_abort_button.grid(row=8, column=1, pady=10)

        # Persistent completion details; the path wraps with the available width.
        self.result_frame = ttk.LabelFrame(self.content, padding=10)
        self.result_frame.grid(row=9, column=0, columnspan=3, padx=10, pady=6, sticky="NSEW")
        self.result_frame.columnconfigure(0, weight=1)
        self.result_label = ttk.Label(self.result_frame, justify="left", wraplength=650)
        self.result_label.grid(row=0, column=0, sticky="EW")
        self.result_label.bind("<Configure>", lambda event: self.result_label.configure(wraplength=max(100, event.width)))
        self.result_actions = ttk.Frame(self.result_frame)
        self.result_actions.grid(row=1, column=0, sticky="EW", pady=(8, 0))
        self.result_actions.bind("<Configure>", lambda _event: self._layout_result_actions())
        self.result_buttons = {}
        for column, (key, command) in enumerate((
            ("result_open_pdf", self.open_result_pdf), ("result_open_folder", self.open_result_folder),
            ("result_retry", self.retry_save), ("result_choose", lambda: self.retry_save(choose_target=True)),
            ("result_discard", self.discard_pending_save),
        )):
            button = ttk.Button(self.result_actions, command=command)
            button.grid(row=0, column=column, padx=(0, 6))
            self.result_buttons[key] = button

        # Version frame
        self.version_frame = ttk.Frame(self.root)
        self.version_frame.grid(row=2, column=0, columnspan=3, padx=10, pady=2, sticky="WE")

        # Initialize version color
        self.version_color = "#808080"
        self.version_label_text = get_ui_string(self.strings, "ver_no_update")
        self.update_label_text = get_ui_string(self.strings, "upd_check")

        current_version = Version.from_str(self.app_settings.settings["version"])
        latest_version = Version.from_str(self.app_settings.settings["newest_version_available"])
        self.update_version_labels_text(latest_version, current_version)

        self.version_label = ttk.Label(self.version_frame, text="...", foreground=self.version_color)
        self.version_label.pack(side="left")

        self.update_label = ttk.Label(self.version_frame, text="...", style="Link.TLabel", cursor="hand2")
        self.update_label.pack(side="left", padx=10)

        # Clicking the update label either forces a re-check (if no cached asset) or starts download directly
        def _on_update_label_click(_event=None):
            try:
                # If updater has a cached download URL for a newer version and label shows install, skip network check
                if (
                    getattr(self.update_checker, "last_download_url", None)
                    and self.update_label_text == get_ui_string(self.strings, "upd_install")
                    and not getattr(self.update_checker, "_active_download", False)
                ):
                    # Optional: ensure cached tag still represents a newer version
                    cached_version_is_current = False
                    try:
                        cached_version_is_current = bool(
                            self.update_checker.last_version_tag
                            and Version.from_str(self.update_checker.last_version_tag) <= Version.from_str(self.app_settings.settings["version"])
                        )
                    except ValueError:
                        cached_version_is_current = False
                    if cached_version_is_current:
                        # Version already current; fallback to check instead
                        import threading as _th

                        _th.Thread(
                            target=lambda: self.check_for_app_updates(current_version, force_check=True),
                            daemon=True,
                        ).start()
                        return
                    verify_sha = self.app_settings.settings.get("verify_sha", "True") == "True"
                    dl = self.update_checker.last_download_url  # type: ignore[assignment]
                    sha = self.update_checker.last_sha_url if verify_sha else None
                    if not dl:
                        # Fallback to forced check if somehow missing
                        import threading as _th

                        _th.Thread(
                            target=lambda: self.check_for_app_updates(current_version, force_check=True),
                            daemon=True,
                        ).start()
                        return
                    import threading

                    threading.Thread(
                        target=lambda: self.update_checker.download_and_run_installer(dl, sha),
                        daemon=True,
                    ).start()
                else:
                    # Force a network re-check (background thread to keep UI responsive)
                    if getattr(self.update_checker, "_active_check", False):
                        return
                    import threading

                    threading.Thread(
                        target=lambda: self.check_for_app_updates(current_version, force_check=True),
                        daemon=True,
                    ).start()
            except Exception as e:
                logging.getLogger("main_window").exception("Failed handling update label click: %s", e)

        self.update_label.bind("<Button-1>", _on_update_label_click)

        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)

        self._apply_theme()
        self.update_all_widget_texts()
        self._poll_system_theme()
        # Set the initial state of the UI based on the settings
        self.sync_settings_to_ui()
        self._update_layout_metrics()
        self._fit_initial_height(width)

    def _resize_content(self, event):
        """Allocate content by viewport width rather than by long label requests."""
        self._layout_content_width(event.width)

    def _layout_content_width(self, width):
        """Wrap content at the allocated width, including before the first mapping."""
        self.content_canvas.itemconfigure(self._content_item, width=width)
        for label, margin in ((self.status_label, 24), (self.options_label, 24), (self.result_label, 48)):
            label.configure(wraplength=max(80, width - margin))
        self._layout_result_actions()

    def _fit_initial_height(self, width):
        """Fit startup content once; later changes preserve the user's window size."""
        width = max(width, self.root.minsize()[0])
        self.title.configure(wraplength=max(80, width - self.header_controls_frame.winfo_reqwidth() - 110))
        self._layout_content_width(max(1, width - self.content_scrollbar.winfo_reqwidth()))
        self.root.update_idletasks()
        self._layout_result_actions()
        self.root.update_idletasks()
        height = (
            self.header_frame.winfo_reqheight() + self.content.winfo_reqheight()
            + self.version_frame.winfo_reqheight() + 4  # Version frame's grid padding.
        )
        height = max(self.root.minsize()[1], min(height, max(1, self.root.winfo_screenheight() - 80)))
        self.root.geometry(f"{width}x{height}")

    def _content_changed(self, _event=None):
        self.content_canvas.configure(scrollregion=self.content_canvas.bbox("all"))

    def _scroll_content(self, event):
        """Scroll only this main window's form, leaving other toplevels independent."""
        if event.widget.winfo_toplevel() is self.root:
            self.content_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _reveal_control(self, event):
        """Keep keyboard-focused form controls visible inside the scrolling content."""
        widget = event.widget
        if not str(widget).startswith(str(self.content) + "."):
            return
        top = widget.winfo_rooty() - self.content.winfo_rooty()
        bottom = top + widget.winfo_height()
        visible_top = self.content_canvas.canvasy(0)
        visible_bottom = visible_top + self.content_canvas.winfo_height()
        height = max(1, self.content.winfo_height())
        if top < visible_top:
            self.content_canvas.yview_moveto(max(0, top - 8) / height)
        elif bottom > visible_bottom:
            self.content_canvas.yview_moveto((bottom + 8 - self.content_canvas.winfo_height()) / height)

    def _layout_result_actions(self):
        """Wrap the existing visible action buttons without changing their states."""
        if not hasattr(self, "result_actions"):
            return
        available = max(1, self.result_actions.winfo_width())
        row = column = used = 0
        for button in self.result_buttons.values():
            if not button.winfo_manager():
                continue
            width = button.winfo_reqwidth() + 6
            if column and used + width > available:
                row, column, used = row + 1, 0, 0
            button.grid(row=row, column=column, sticky="w", pady=3)
            column += 1
            used += width

    def _update_layout_metrics(self):
        """Use font/control measurements for a bounded minimum, preserving user size."""
        self.root.update_idletasks()
        width = max(
            max(self.label_pdf_file.winfo_reqwidth(), self.label_search_str.winfo_reqwidth())
            + self.ui_font.measure("M" * 14) + self.browse_button.winfo_reqwidth() + 60,
            self.button_filter.winfo_reqwidth() + self.button_watermark.winfo_reqwidth() + 60,
            self.logo_label.winfo_reqwidth() + self.header_controls_frame.winfo_reqwidth() + self.ui_font.measure("M" * 20) + 50,
            *(button.winfo_reqwidth() + 64 for button in self.result_buttons.values()),
        )
        height = self.header_frame.winfo_reqheight() + self.version_frame.winfo_reqheight() + self.ui_font.metrics("linespace") * 8
        self.root.minsize(min(width, max(1, self.root.winfo_screenwidth() - 40)),
                          min(height, max(1, self.root.winfo_screenheight() - 80)))
        self.title.configure(wraplength=max(100, self.root.winfo_width() - self.header_controls_frame.winfo_reqwidth() - 100))
        self._layout_result_actions()

    def _options_text(self):
        """Describe committed options, using the retained job while it owns an output."""
        job = self._job or getattr(self, "_pending_save", None)
        options = job.options if job else self._collect_processing_options()
        def text(key):
            return get_ui_string(self.strings, key)
        parts = [text({MarkingMode.ALL_MATCHES: "opt_all", MarkingMode.DISTINGUISH_NAMES: "opt_distinguish",
                       MarkingMode.NAMED_MATCHES: "opt_named"}[options.marking_mode])]
        if options.marking_mode != MarkingMode.ALL_MATCHES:
            parts.append(self.get_plural_string("option_names", len(options.names)).format(len(options.names)))
        parts.append(text("opt_relevant" if options.only_relevant else "opt_any_line"))
        watermark = dict(options.watermark)
        watermark_key = "opt_watermark_off"
        if watermark.get("watermark_enabled") == "True":
            watermark_key = "opt_watermark_on" if str(watermark.get("watermark_text", "")).strip() else "opt_watermark_empty"
        parts.append(text(watermark_key))
        parts.append(text("opt_ocr_auto" if options.ocr_enabled else "opt_ocr_off"))
        return text("opt_job" if job else "opt_active") + " " + " · ".join(parts)

    def _render_options(self):
        """Refresh the summary after a committed edit or job ownership change."""
        if hasattr(self, "options_label"):
            self.options_label.configure(text=self._options_text())

    def _persist_setting(self, key, value):
        if not getattr(self, "_syncing_settings", False):
            self.app_settings.update_setting(key, value)
            self._render_options()

    def sync_setting_variables(self, settings):
        """Apply a full or partial settings snapshot without persistence traces."""
        variables = {
            "search_str": "search_phrase_var", "mark_only_relevant_lines": "relevant_lines_var",
            "names": "names_var", "marking_mode": "marking_mode_var",
            "highlight_color_default": "highlight_color_default_var",
            "highlight_color_names": "highlight_color_names_var",
            "language": "language_var", "theme_mode": "theme_mode_var",
        }
        previous = getattr(self, "_syncing_settings", False)
        self._syncing_settings = True
        try:
            for key, attribute in variables.items():
                if key in settings:
                    getattr(self, attribute).set(settings[key])
        finally:
            self._syncing_settings = previous
        self._render_options()

    def sync_settings_to_ui(self):
        """Synchronize initialization/reset, including translated auxiliary UI."""
        settings = self.app_settings.snapshot()
        self.sync_setting_variables(settings)
        self._apply_theme(settings["theme_mode"])
        self.on_language_change(settings["language"])
        if hasattr(self, "marking_preview"):
            self._schedule_preview_prefetch()

    def reset_settings(self):
        """Discard drafts and synchronize defaults while preserving the source PDF."""
        if self.output_options_locked or self.update_checker._active_download:
            return
        self.filter_dialog.close()
        self.watermark_dialog.close(close_preview=True)
        self.preview_window_handler.last_watermark_data = {}
        self.preview_window_handler.last_origin = None
        self.app_settings.reset_to_defaults()
        self.sync_settings_to_ui()

    def _show_settings_notices(self):
        """Explain filesystem failures only after startup, on the Tk thread."""
        if self._closing:
            return
        for notice in self.app_settings.take_notices():
            text = get_ui_string(self.strings, f"settings_{notice.kind}").format(
                path=notice.path, backup=notice.backup_path,
            )
            show_info(self, get_ui_string(self.strings, "settings_notice"), text)

    def update_all_widget_texts(self):
        """
        Update all widget texts from self.strings. Call after changing language or initializing UI.
        """
        self.root.title(get_ui_string(self.strings, "title"))
        self.title.config(text=get_ui_string(self.strings, "title"))
        self.label_pdf_file.config(text=get_ui_string(self.strings, "pdf_file"))
        self.label_search_str.config(text=get_ui_string(self.strings, "search_term"))
        self.checkbox_relevant_lines.config(text=get_ui_string(self.strings, "mark_only_relevant"))
        self._render_operation()
        self.browse_button.config(text=get_ui_string(self.strings, "btn_browse"))
        self.button_filter.config(text=get_ui_string(self.strings, "btn_filter"))
        self.button_watermark.config(text=get_ui_string(self.strings, "btn_watermark"))
        self.language_menu.config(text=get_ui_string(self.strings, "select_language"))
        self.theme_menu.entryconfigure(0, label=get_ui_string(self.strings, "theme_system"))
        self.theme_menu.entryconfigure(1, label=get_ui_string(self.strings, "theme_light"))
        self.theme_menu.entryconfigure(2, label=get_ui_string(self.strings, "theme_dark"))
        self._set_theme_button_icon()

        # Update version-related text
        self.update_version_labels()

        # Tooltips
        Tooltip(self.entry_file, text=getattr(self, "input_file_full_path", "") or get_ui_string(self.strings, "select_pdf"))
        Tooltip(self.label_pdf_file, text=get_ui_string(self.strings, "select_pdf"))
        Tooltip(self.label_search_str, text=get_ui_string(self.strings, "enter_club"))
        Tooltip(self.entry_search_str, text=get_ui_string(self.strings, "enter_club"))
        Tooltip(self.checkbox_relevant_lines, text=get_ui_string(self.strings, "only_highlight_lines"))
        Tooltip(self.button_filter, text=get_ui_string(self.strings, "configure_filter"))
        Tooltip(self.button_watermark, text=get_ui_string(self.strings, "configure_watermark"))
        Tooltip(self.start_abort_button, text=get_ui_string(self.strings, "start_cancel"))
        Tooltip(self.language_menu, text=get_ui_string(self.strings, "select_language"))
        Tooltip(self.theme_button, text=get_ui_string(self.strings, "theme_menu_tooltip"))

    def _load_theme_icon_images(self) -> dict[str, ImageTk.PhotoImage]:
        icons = {}
        icon_dir = self.paths.bundle_dir / "assets" / "light_dark"
        for theme_name in ("light", "dark"):
            icon_path = icon_dir / f"{theme_name}_cut.png"
            try:
                with Image.open(icon_path) as icon:
                    image = icon.resize(THEME_ICON_SIZE, Image.Resampling.LANCZOS)
                    icons[theme_name] = ImageTk.PhotoImage(image)
            except Exception as e:
                logging.getLogger("main_window").debug("Could not load theme icon %s: %s", icon_path, e)
        return icons

    def _open_theme_menu(self) -> None:
        self._configure_menu(self.theme_menu, getattr(self, "_current_theme_colors", get_theme_colors("light")))
        x = self.theme_button.winfo_rootx()
        y = self.theme_button.winfo_rooty() + self.theme_button.winfo_height()
        try:
            self.theme_menu.tk_popup(x, y)
        finally:
            self.theme_menu.grab_release()

    def _set_theme_button_icon(self, theme_mode: str | None = None) -> None:
        effective_theme = get_effective_theme(theme_mode or self.app_settings.settings.get("theme_mode", "system"))
        image = getattr(self, "theme_icon_images", {}).get(effective_theme)
        if image is None:
            self.theme_button.configure(image="", text="")
            return

        self.theme_button.configure(image=image, text="", compound="left")
        setattr(self.theme_button, "image", image)

    def _apply_theme(self, theme_mode: str | None = None) -> None:
        theme_mode = theme_mode or self.app_settings.settings.get("theme_mode", "system")
        effective_theme = get_effective_theme(theme_mode)
        theme_name = "breeze-dark" if effective_theme == "dark" else "breeze"
        try:
            self.root.tk.call("package", "require", f"ttk::theme::{theme_name}")
        except Exception as e:
            logging.getLogger("main_window").debug("Could not explicitly load %s theme: %s", theme_name, e)
        self.style.theme_use(theme_name)

        colors = get_theme_colors(effective_theme)
        self._current_theme_colors = colors
        self._current_effective_theme = effective_theme
        setattr(self.root, "_hsph_effective_theme", effective_theme)
        self._set_tk_palette(colors)
        self._configure_ttk_styles(colors)
        self.apply_theme_to_window(self.root)

        if hasattr(self, "theme_menu"):
            self._configure_menu(self.theme_menu, colors)
        if hasattr(self, "language_menu"):
            self._configure_widget_menu(self.language_menu, colors)

        self._current_effective_theme = effective_theme
        if hasattr(self, "theme_button"):
            self._set_theme_button_icon(theme_mode)
        if hasattr(self, "update_label"):
            self.update_label.configure(style="Link.TLabel")
        if hasattr(self, "version_label"):
            self.update_version_labels()
        self._apply_theme_to_open_windows()

    def _set_tk_palette(self, colors: ThemeColors) -> None:
        try:
            self.root.tk_setPalette(
                background=colors.background,
                foreground=colors.foreground,
                activeBackground=colors.active_background,
                activeForeground=colors.select_foreground,
                highlightColor=colors.select_background,
                selectBackground=colors.select_background,
                selectForeground=colors.select_foreground,
            )
        except Exception as e:
            logging.getLogger("main_window").debug("Could not update Tk palette: %s", e)

    def _configure_ttk_styles(self, colors: ThemeColors) -> None:
        label_font = getattr(self, "ui_font", ("Arial", 10))
        title_font = getattr(self, "heading_font", ("Arial", 16, "bold"))
        if hasattr(self, "ui_font"):
            # Breeze also sets widget defaults, which override TLabel/TEntry styles.
            self.root.option_add("*font", self.ui_font)

        self.style.configure(".", font=label_font, background=colors.background, foreground=colors.foreground)
        self.style.configure("TFrame", background=colors.background)
        self.style.configure("TLabelframe", background=colors.background, foreground=colors.foreground)
        self.style.configure("TLabelframe.Label", background=colors.background, foreground=colors.foreground)
        self.style.configure("TLabel", font=label_font, background=colors.background, foreground=colors.foreground)
        self.style.configure("Title.TLabel", font=title_font, background=colors.background, foreground=colors.foreground)
        self.style.configure("Logo.TLabel", background=colors.background, foreground=colors.foreground)
        self.style.configure("Link.TLabel", font=label_font, background=colors.background, foreground=colors.muted_foreground)
        self.style.configure("TButton", font=label_font, borderwidth="4", foreground=colors.foreground)
        self.style.configure("TMenubutton", font=label_font, borderwidth="4", foreground=colors.foreground)
        self.style.configure("TCheckbutton", font=label_font, background=colors.background, foreground=colors.foreground)
        self.style.configure("TRadiobutton", font=label_font, background=colors.background, foreground=colors.foreground)
        self.style.configure(
            "TEntry",
            font=label_font,
            borderwidth="2",
            fieldbackground=colors.field_background,
            foreground=colors.foreground,
            insertcolor=colors.foreground,
        )
        self.style.configure(
            "TCombobox",
            fieldbackground=colors.field_background,
            foreground=colors.foreground,
            selectbackground=colors.select_background,
            selectforeground=colors.select_foreground,
        )
        self.style.configure(
            "TSpinbox",
            fieldbackground=colors.field_background,
            foreground=colors.foreground,
            insertcolor=colors.foreground,
        )
        self.style.configure("Horizontal.TProgressbar", thickness=20)
        self.style.map(
            "TEntry",
            foreground=[("disabled", colors.muted_foreground), ("readonly", colors.foreground)],
            fieldbackground=[("disabled", colors.field_background), ("readonly", colors.field_background)],
        )
        self.style.map(
            "TCombobox",
            foreground=[("readonly", colors.foreground), ("disabled", colors.muted_foreground)],
            fieldbackground=[("readonly", colors.field_background), ("disabled", colors.field_background)],
        )
        self.style.map(
            "TSpinbox",
            foreground=[("disabled", colors.muted_foreground)],
            fieldbackground=[("disabled", colors.field_background)],
        )

    def apply_theme_to_window(self, window) -> None:
        """Apply the active theme to a Tk/Toplevel and classic Tk descendants."""
        colors = getattr(self, "_current_theme_colors", get_theme_colors(getattr(self, "_current_effective_theme", "light")))
        effective_theme = getattr(self, "_current_effective_theme", "light")
        setattr(window, "_hsph_effective_theme", effective_theme)
        set_windows_title_bar_theme(window, effective_theme)
        self._configure_classic_widget(window, colors)
        for child in window.winfo_children():
            self._apply_theme_to_widget_tree(child, colors)

    def _apply_theme_to_widget_tree(self, widget, colors: ThemeColors) -> None:
        if widget.__class__.__module__.startswith("tkinterweb"):
            return
        self._configure_classic_widget(widget, colors)
        self._configure_widget_menu(widget, colors)
        for child in widget.winfo_children():
            self._apply_theme_to_widget_tree(child, colors)

    def _configure_classic_widget(self, widget, colors: ThemeColors) -> None:
        try:
            widget_class = widget.winfo_class()
        except Exception:
            return

        if widget_class in {"Tk", "Toplevel", "Frame", "Labelframe", "Canvas"}:
            self._safe_configure(widget, background=colors.background, highlightbackground=colors.background)
        elif widget_class == "Button":
            swatch_color = getattr(widget, "_hsph_swatch_color", None)
            if swatch_color:
                self._safe_configure(
                    widget,
                    background=swatch_color,
                    activebackground=swatch_color,
                    highlightbackground=colors.border,
                    highlightcolor=colors.select_background,
                    borderwidth=1,
                    relief="sunken" if getattr(widget, "_hsph_swatch_selected", False) else "flat",
                    overrelief="raised",
                    padx=0,
                    pady=0,
                )
                return
            self._safe_configure(
                widget,
                background=colors.background,
                foreground=colors.foreground,
                activebackground=colors.background,
                activeforeground=colors.foreground,
                highlightbackground=colors.background,
                highlightcolor=colors.select_background,
                borderwidth=0,
                relief="flat",
                overrelief="flat",
                padx=0,
                pady=0,
            )
        elif widget_class == "Label":
            self._safe_configure(
                widget,
                background=colors.background,
                foreground=colors.foreground,
                highlightthickness=0,
                borderwidth=0,
            )
        elif widget_class == "Text":
            self._safe_configure(
                widget,
                background=colors.field_background,
                foreground=colors.foreground,
                insertbackground=colors.foreground,
                selectbackground=colors.select_background,
                selectforeground=colors.select_foreground,
                highlightbackground=colors.border,
                highlightcolor=colors.select_background,
            )
        elif widget_class == "Menu":
            self._configure_menu(widget, colors)

    def _configure_widget_menu(self, widget, colors: ThemeColors) -> None:
        try:
            menu_name = widget.cget("menu")
        except Exception:
            return
        if not menu_name:
            return
        try:
            self._configure_menu(widget.nametowidget(menu_name), colors)
        except Exception:
            return

    def _configure_menu(self, menu, colors: ThemeColors) -> None:
        self._safe_configure(
            menu,
            background=colors.field_background,
            foreground=colors.foreground,
            activebackground=colors.active_background,
            activeforeground=colors.select_foreground,
            disabledforeground=colors.muted_foreground,
            selectcolor=colors.select_background,
            borderwidth=1,
            relief="solid",
        )

    @staticmethod
    def _safe_configure(widget, **kwargs) -> None:
        try:
            widget.configure(**kwargs)
        except TclError:
            for key, value in kwargs.items():
                try:
                    widget.configure(**{key: value})
                except TclError:
                    continue

    def _apply_theme_to_open_windows(self) -> None:
        for owner_name in ("filter_dialog", "watermark_dialog", "preview_window_handler", "dev_tools"):
            owner = getattr(self, owner_name, None)
            window = getattr(owner, "window", None)
            if window is None:
                continue
            try:
                if window.winfo_exists():
                    self.apply_theme_to_window(window)
                    if hasattr(owner, "refresh_theme"):
                        owner.refresh_theme()
            except Exception as e:
                logging.getLogger("main_window").debug("Could not theme %s window: %s", owner_name, e)

    def _on_theme_mode_change(self, mode: str) -> None:
        if getattr(self, "_syncing_settings", False):
            return
        self.app_settings.update_setting("theme_mode", mode)
        self._apply_theme(mode)

    def _poll_system_theme(self) -> None:
        if not getattr(self.root, "tk", None):
            return

        if self.app_settings.settings.get("theme_mode", "system") == "system":
            new_effective_theme = get_effective_theme("system")
            if new_effective_theme != getattr(self, "_current_effective_theme", None):
                self._apply_theme("system")

        try:
            self.root.after(2000, self._poll_system_theme)
        except RuntimeError:
            return

    def open_filter_window(self):
        """Open the filter configuration dialog."""
        if not self.output_options_locked:
            self.filter_dialog.open()

    def close(self) -> None:
        """Cancel owned work and let the main loop observe asynchronous cleanup."""
        if self._closing:
            return
        self._closing = True
        self.discard_pending_save()
        self.request_cancel_processing()
        self._download_cancel.set()
        self._closing_previews = []
        preview = getattr(self, "marking_preview", None) or getattr(self.filter_dialog, "_preview", None)
        if preview is not None:
            # close() detaches Tk timers; its process cleanup runs outside Tk.
            preview.close()
            if preview.cleanup_worker is not None:
                self._closing_previews.append(preview.cleanup_worker)
        for owner in (self.preview_window_handler, self.filter_dialog, self.watermark_dialog):
            with suppress(TclError):
                owner.close()
        self._closing_previews.extend(getattr(self, "_preview_cleanups", ()))
        window = getattr(self.dev_tools, "window", None)
        if window is not None:
            with suppress(TclError):
                window.destroy()
        # Release nested wait_window calls, including processing prompts.
        for child in self.root.winfo_children():
            if child.winfo_class() == "Toplevel":
                with suppress(TclError):
                    child.destroy()
        self._set_inputs_locked()
        self._render_operation()

    def open_watermark_window(self):
        """Open the watermark configuration dialog."""
        if not self.output_options_locked:
            self.watermark_dialog.open()

    def preview_watermark(self, enabled, text, color, size, position, x_ratio, y_ratio, preview_page, origin=None, force_open=True):
        """Preview watermark (delegate to preview window handler)."""
        self.preview_window_handler.preview_watermark(enabled, text, color, size, position, x_ratio, y_ratio, preview_page, origin, force_open)

    @property
    def current_preview_page(self):
        """Get current preview page."""
        return self.preview_window_handler.current_page

    @current_preview_page.setter
    def current_preview_page(self, value):
        """Set current preview page."""
        self.preview_window_handler.current_page = value

    def change_preview_page(self, delta: int, reset: bool = False):
        """Change preview page (delegate to preview window handler)."""
        self.preview_window_handler.change_page(delta, reset)

    def update_version_labels_text(self, latest_version: Version | None | bool, current_version: Version = Version.from_str(VERSION_STR)):
        """Update version label text and color based on update status."""
        if latest_version is None or latest_version is False:
            self.version_label_text = get_ui_string(self.strings, "ver_update_failed")
            self.version_color = "#9d6363"
            self.update_label_text = get_ui_string(self.strings, "upd_check")
        elif latest_version and latest_version > current_version:
            self.version_label_text = get_ui_string(self.strings, "ver_new")
            self.version_color = "#ff9f14"
            self.update_label_text = get_ui_string(self.strings, "upd_install")
        else:
            self.version_label_text = get_ui_string(self.strings, "ver_no_update")
            self.version_color = "#808080"
            self.update_label_text = get_ui_string(self.strings, "upd_check")

    def update_version_labels(self):
        """Update version labels in the UI."""
        self.version_label.config(text=self.version_label_text.format(self.app_settings.settings["version"]), foreground=self.version_color)
        self.update_label.config(text=self.update_label_text)
        self.root.update_idletasks()

    def on_version_update(self, latest_version, current_version):
        """Callback for when version update info is received."""
        # Schedule on main thread since this is called from update check thread
        try:
            # If the root has been destroyed (common in screenshot mode teardown), silently ignore
            if not getattr(self.root, "tk", None):
                return
            self.root.after_idle(lambda: self._safe_update_version_info(latest_version, current_version))
        except RuntimeError:
            # Root is gone; ignore
            return

    def _safe_update_version_info(self, latest_version, current_version):
        """Wrapper that swallows RuntimeError if the Tk main loop/root is already gone."""
        try:
            self._update_version_info(latest_version, current_version)
        except RuntimeError:
            # Tkinter can raise 'main thread is not in main loop' if updating after destroy
            pass

    def _update_version_info(self, latest_version, current_version):
        """Update version info on main thread."""
        self.update_version_labels_text(latest_version, current_version)
        self.update_version_labels()

    def on_language_change(self, language: str):
        """
        Change the language of the application.

        Args:
            language (str): The language code to switch to.
        """
        self._, self.n_ = setup_translation(language)
        self.init_translatable_strings()
        self.update_all_widget_texts()
        # In screenshot mode, keep a neutral waiting status for a cleaner image
        if not self.output_options_locked and not getattr(self, "last_processing_result", None):
            self._set_status("status_waiting" if os.getenv("HSPH_SCREENSHOT_MODE") in ("1", "true", "True") else "status_language_changed")
        self._render_operation()
        self.update_version_labels_text(
            Version.from_str(self.app_settings.settings["newest_version_available"]), Version.from_str(self.app_settings.settings["version"])
        )
        self.update_version_labels()
        self.root.update_idletasks()
        # Refresh Dev Tools window strings if it's open
        try:
            if hasattr(self, "dev_tools") and self.dev_tools:
                # dev_tools may be created earlier; ensure it refreshes its UI
                try:
                    self.dev_tools.refresh_ui_strings()
                except Exception as e:
                    logging.getLogger("main_window").exception("Failed to refresh dev tools strings: %s", e)
        except Exception as e:
            logging.getLogger("main_window").exception("Unexpected error while refreshing dev tools: %s", e)
        # Refresh filter and watermark dialogs if they're open
        try:
            if hasattr(self, "filter_dialog") and self.filter_dialog:
                try:
                    self.filter_dialog.refresh_ui_strings()
                except Exception as e:
                    logging.getLogger("main_window").exception("Failed to refresh filter dialog strings: %s", e)
        except Exception as e:
            logging.getLogger("main_window").exception("Unexpected error while refreshing filter dialog: %s", e)
        try:
            if hasattr(self, "watermark_dialog") and self.watermark_dialog:
                try:
                    self.watermark_dialog.refresh_ui_strings()
                except Exception as e:
                    logging.getLogger("main_window").exception("Failed to refresh watermark dialog strings: %s", e)
        except Exception as e:
            logging.getLogger("main_window").exception("Unexpected error while refreshing watermark dialog: %s", e)

        self.preview_window_handler.refresh_ui_strings()
        if hasattr(self, "content_canvas"):
            self._update_layout_metrics()

    def browse_file(self):
        """
        Opens a file dialog to browse and select a PDF file.
        """
        if self.output_options_locked:
            return
        self.status_var.set(get_ui_string(self.strings, "status_importing"))
        self.root.update_idletasks()
        file_path = filedialog.askopenfilename(
            filetypes=((get_ui_string(self.strings, "file_filter_pdf"), "*.pdf"), (get_ui_string(self.strings, "file_filter_all"), "*.*"))
        )
        if file_path:
            file_name = Path(file_path).name
            self.pdf_file_var.set(file_name)  # Display only the file name
            self.input_file_full_path = file_path  # Store full path for processing
            Tooltip(self.entry_file, text=file_path)
            self.marking_preview.prepare(file_path)
            self._prefetch_preview()
            self.ocr_detection_path = file_path
            self.ocr_detection_result = None
            self.status_var.set(get_ui_string(self.strings, "status_imported"))
            if self.app_settings.settings.get("ocr_enabled", "True") == "True":
                threading.Thread(target=self._detect_ocr_requirement, args=(file_path,), daemon=True).start()
        else:
            self.status_var.set(get_ui_string(self.strings, "status_waiting"))
            self.root.update_idletasks()

    def _schedule_preview_prefetch(self, *_):
        if getattr(self, "_syncing_settings", False):
            return
        if self._preview_prefetch_timer is not None:
            self.root.after_cancel(self._preview_prefetch_timer)
        self._preview_prefetch_timer = self.root.after(300, self._prefetch_preview)

    def _prefetch_preview(self):
        if self._preview_prefetch_timer is not None:
            with suppress(TclError):
                self.root.after_cancel(self._preview_prefetch_timer)
        self._preview_prefetch_timer = None
        if not self._closing and self.filter_dialog.window is None:
            self.marking_preview.request(self.filter_dialog.capture_request(), immediate=True)

    def _detect_ocr_requirement(self, file_path: str) -> None:
        """Detect scanned PDFs in the background after file import."""
        self.root.after_idle(lambda: self._set_ocr_checking_status(file_path))
        try:
            needs_ocr = pdf_needs_ocr(file_path)
        except Exception as e:
            logging.getLogger("main_window").exception("Failed to detect OCR requirement for %s: %s", file_path, e)
            needs_ocr = False
        self.root.after_idle(lambda: self._apply_ocr_detection_result(file_path, needs_ocr))

    def _set_ocr_checking_status(self, file_path: str) -> None:
        if getattr(self, "input_file_full_path", None) == file_path and not self.output_options_locked:
            self.status_var.set(get_ui_string(self.strings, "status_ocr_checking"))

    def _apply_ocr_detection_result(self, file_path: str, needs_ocr: bool) -> None:
        if getattr(self, "input_file_full_path", None) != file_path:
            return
        self.ocr_detection_path = file_path
        self.ocr_detection_result = needs_ocr
        if self.output_options_locked:
            return
        status_key = "status_ocr_needed" if needs_ocr else "status_imported"
        self.status_var.set(get_ui_string(self.strings, status_key))

    @property
    def output_options_locked(self):
        """Whether output-affecting edits and reset are currently unavailable."""
        return bool(self._job or self.download_active or self._closing)

    def _set_inputs_locked(self):
        locked = self.output_options_locked
        preview = getattr(self, "marking_preview", None)
        if preview is not None:
            preview.set_paused(bool(self._job))
        for name in ("browse_button", "entry_search_str", "checkbox_relevant_lines", "button_filter", "button_watermark"):
            getattr(self, name).configure(state="disabled" if locked else "normal")
        self.entry_file.configure(state="disabled" if locked else "readonly")
        for owner in (self.filter_dialog, self.watermark_dialog, self.dev_tools):
            owner.set_busy(locked)

    def _set_status(self, key, *args):
        self._status = (key, args)
        text = (self.get_plural_string(key, args[2]) if key == "processed_pages" else get_ui_string(self.strings, key))
        self.status_var.set(text.format(*args))

    def _render_operation(self):
        job = self._job
        cancelling = bool(job and job.cancel.is_set()) or (self.download_active and self._download_cancel.is_set())
        busy = bool(job or self.download_active)
        self.start_abort_button.configure(
            text=get_ui_string(self.strings, "btn_cancelling" if cancelling else "btn_abort" if busy else "btn_start"),
            command=self.request_cancel_processing if job else self.abort_download if busy else self.start_processing,
            state="disabled" if cancelling or self._closing or (getattr(self, "_pending_save", None) and not busy) else "normal",
        )
        key, args = self._status
        self._set_status(key, *args)
        self._render_result()
        self._render_options()

    def start_processing(self):
        """Validate and capture one job before starting its owned worker."""
        if self.output_options_locked or getattr(self, "_pending_save", None) or getattr(self.update_checker, "_active_download", False):
            return
        input_file = getattr(self, "input_file_full_path", None)
        options = self._collect_processing_options()
        if not input_file or not options.search_str.strip():
            show_error(self, get_ui_string(self.strings, "error"), get_ui_string(self.strings, "val_all_required"))
            return
        if options.marking_mode != MarkingMode.ALL_MATCHES and not options.names:
            show_error(self, get_ui_string(self.strings, "error"), get_ui_string(self.strings, "flt_names_required"))
            return
        self.app_settings.update_setting("search_str", options.search_str)
        self._next_job_id += 1
        job = ProcessingJob(self._next_job_id, input_file, options)
        self._job = job
        self.processing_active = True
        self._set_status("status_preparing")
        self._set_inputs_locked()
        self._render_operation()
        job.worker = threading.Thread(target=self.process_pdf, args=(job,), daemon=True)
        try:
            job.worker.start()
        except Exception as exc:
            logging.getLogger(__name__).exception("Could not start PDF processing worker")
            job.result = ("failed", str(exc))
            job.worker = None
            self.finalize_processing(job)

    def request_cancel_processing(self):
        """Request cancellation; resource ownership stays with the running job."""
        if self._job is None:
            return
        self._job.cancel.set()
        self._job.state = "cancelling"
        self._set_status("status_cancelling")
        self._render_operation()

    def _post_job(self, job, kind, *args):
        self._ui_messages.put((job.identifier, kind, args))

    def _request_job_ui(self, job, callback):
        request = GuiRequest(callback)
        self._post_job(job, "request", request)
        while not request.ready.wait(0.05):
            self._raise_if_cancelled(job)
        self._raise_if_cancelled(job)
        if request.error:
            raise request.error
        return request.value

    @staticmethod
    def _raise_if_cancelled(job):
        if job.cancel.is_set():
            raise OcrCancelled()

    def _poll_work(self):
        """Drain worker messages and observe termination without waiting in Tk."""
        # Bound each batch so a busy worker cannot starve Tk events.
        for _ in range(100):
            try:
                identifier, kind, args = self._ui_messages.get_nowait()
            except queue.Empty:
                break
            if kind == "download_request":
                request = args[0]
                try:
                    if not self._closing:
                        request.value = request.callback()
                except Exception as exc:
                    request.error = exc
                finally:
                    request.ready.set()
                continue
            if kind == "download":
                if identifier == self._download_id and self.download_active and not self._closing:
                    args[0]()
                continue
            job = self._job
            if job is None or identifier != job.identifier:
                if kind == "request":
                    args[0].ready.set()
                continue
            if kind == "started":
                self._result_job = None
                self._render_result()
            elif kind == "result":
                job.result = args
            elif kind == "request":
                request = args[0]
                try:
                    if not job.cancel.is_set() and not self._closing:
                        job.dialog_open = True
                        request.value = request.callback()
                except Exception as exc:
                    request.error = exc
                finally:
                    job.dialog_open = False
                    request.ready.set()
            elif not job.cancel.is_set() and not self._closing:
                if kind == "phase":
                    job.state = args[0]
                    self._set_status(args[1])
                    if job.state == "saving":
                        self._start_indeterminate_progress_gui()
                elif kind == "pages":
                    self._set_determinate_progress()
                    self.progress_bar["value"] = args[0] / args[1] * 100
                    self._set_status("processed_pages", *args)
                elif kind == "ocr":
                    self._set_determinate_progress()
                    self.progress_bar["value"] = args[0] / max(1, args[1]) * 100
                    self._set_status("status_ocr_processing", *args)
        job = self._job
        if job and job.result is not None and not job.dialog_open and (job.worker is None or not job.worker.is_alive()):
            self.finalize_processing(job)
        if self._closing and not self._job and not getattr(self.update_checker, "_active_download", False):
            if not any(worker.is_alive() for worker in getattr(self, "_closing_previews", ())):
                self.root.destroy()
                return
        self.root.after(30, self._poll_work)
        self._show_settings_notices()

    def process_pdf(self, job):
        """Run PDF work using only job-owned input and cancellation."""
        document = None
        ocr_temp_file = None
        outcome = ("aborted",)
        try:
            self._raise_if_cancelled(job)
            self.paths.is_valid_path(job.input_file)
            document = Document(job.input_file)
            if document.is_encrypted:
                outcome = ("error_key", "val_pdf_protected")
                return
            options = job.options
            needs_ocr = options.needs_ocr
            use_ocr = options.ocr_enabled and (needs_ocr if needs_ocr is not None else document_needs_ocr(document))
            if use_ocr:
                def confirm():
                    language = self._confirm_ocr_processing(options.ocr_language)
                    if language is not None and not job.cancel.is_set() and not self._closing:
                        self.app_settings.update_setting("ocr_language", language)
                        job.options = replace(options, ocr_language=language)
                        self._render_options()
                        return job.options
                    return None
                options = self._request_job_ui(job, confirm)
                if options is None:
                    raise OcrCancelled()
            output_file = self._request_job_ui(job, lambda: self._ask_output_file(job.input_file))
            if not output_file:
                raise OcrCancelled()
            job.output_file = output_file
            job.target_signature = self._target_signature(output_file)
            self._raise_if_cancelled(job)
            job.started = True
            self._post_job(job, "started")
            if use_ocr:
                try:
                    tessdata = ensure_bundled_tessdata(self.paths.ocr_tessdata_dir, options.ocr_language)
                except FileNotFoundError:
                    outcome = ("error_key", "val_ocr_assets_missing")
                    return
                self._raise_if_cancelled(job)
                ocr_temp_file = self._create_temp_pdf_path()
                document.close()
                document = None
                self._post_job(job, "phase", "processing", "status_processing")
                create_searchable_ocr_pdf_in_process(
                    job.input_file, ocr_temp_file, tessdata_dir=tessdata,
                    language=options.ocr_language, dpi=options.ocr_dpi,
                    progress_callback=lambda current, total: self._post_job(job, "ocr", current, total),
                    is_cancelled=job.cancel.is_set,
                )
                self._raise_if_cancelled(job)
                document = Document(ocr_temp_file)
            self._post_job(job, "phase", "processing", "status_processing")
            job.counts = self._process_document_pages(job, document, options)
            total_matches, total_skipped, watermark_pages, marked_lines = job.counts
            self._raise_if_cancelled(job)
            if total_matches - total_skipped <= 0 and watermark_pages == 0:
                outcome = ("info_key", "val_nothing")
                return
            job.document = document
            job.ocr_temp_file = ocr_temp_file
            self._post_job(job, "phase", "saving", "status_saving")
            try:
                save_result = self._save_processed_document(job, document, output_file, bool(ocr_temp_file), options)
            except OcrCancelled:
                raise
            except Exception as exc:
                logging.getLogger(__name__).exception("PDF saving failed; retaining calculated result")
                outcome = ("save_failed", str(exc))
                document = ocr_temp_file = None  # Ownership transfers to the pending job.
                return
            outcome = ("saved", total_matches, total_skipped, save_result, watermark_pages, marked_lines, output_file)
        except OcrCancelled:
            outcome = ("aborted",)
        except Exception as exc:
            logging.getLogger(__name__).exception("PDF processing failed")
            outcome = ("failed", str(exc))
        finally:
            if document is not None:
                with suppress(Exception):
                    document.close()
            self._safe_unlink(ocr_temp_file)
            if outcome[0] != "save_failed":
                job.document = job.ocr_temp_file = None
            self._post_job(job, "result", *outcome)

    def _collect_processing_options(self):
        settings = self.app_settings.snapshot()
        return ProcessingOptions(
            search_str=self.search_phrase_var.get(), only_relevant=bool(self.relevant_lines_var.get()),
            names=parse_names(self.names_var.get()), marking_mode=MarkingMode(self.marking_mode_var.get()),
            highlight_color_default=self.highlight_color_default_var.get(),
            highlight_color_names=self.highlight_color_names_var.get(),
            watermark=tuple((key, settings[key]) for key in (
                "watermark_enabled", "watermark_text", "watermark_color", "watermark_size", "watermark_position",
                "watermark_x_ratio", "watermark_y_ratio",
            ) if key in settings),
            ocr_enabled=settings.get("ocr_enabled", "True") == "True",
            ocr_language=settings.get("ocr_language", "deu"), ocr_dpi=int(settings.get("ocr_dpi", 300)),
            ocr_reduce_large_outputs=settings.get("ocr_reduce_large_outputs", "True") == "True",
            needs_ocr=self.ocr_detection_result if self.ocr_detection_path == getattr(self, "input_file_full_path", None) else None,
        )

    def _process_document_pages(self, job, document, options):
        total_matches = total_skipped = watermark_pages = marked_lines = 0
        watermark = dict(options.watermark)
        for index in range(len(document)):
            self._raise_if_cancelled(job)
            matches, skipped, lines = highlight_matching_data(
                page=document[index], search_str=options.search_str, only_relevant=options.only_relevant,
                names=options.names, marking_mode=options.marking_mode,
                highlight_color_default=options.highlight_color_default, highlight_color_names=options.highlight_color_names,
            )
            marked_lines += lines
            total_matches += matches
            total_skipped += skipped
            self._raise_if_cancelled(job)
            if watermark_pdf_page(document[index], watermark):
                watermark_pages += 1
            self._post_job(job, "pages", index + 1, len(document), total_matches, total_skipped)
        return total_matches, total_skipped, watermark_pages, marked_lines

    def _save_processed_document(self, job, document, output_file, ocr_used, options):
        """Stage a completed PDF before atomically committing the approved target."""
        self._raise_if_cancelled(job)
        target = Path(output_file)
        self._validate_output_target(job.input_file, target)
        # Stage beside the destination so the final replace stays on one filesystem.
        handle = tempfile.NamedTemporaryFile(prefix=".hsph-", suffix=".pdf", dir=target.parent, delete=False)
        staged = handle.name
        prepared = None
        try:
            handle.close()
            if not ocr_used:
                save_compact_pdf(document, staged)
                result = None
            else:
                prepared = self._create_temp_pdf_path()
                document.save(prepared)
                self._raise_if_cancelled(job)
                result = save_pdf_path_in_process(
                    prepared, staged, original_pdf_path=job.input_file, ocr_used=True,
                    reduce_large_outputs=options.ocr_reduce_large_outputs, is_cancelled=job.cancel.is_set,
                )
            with open(staged, "r+b") as completed:
                os.fsync(completed.fileno())
            self._raise_if_cancelled(job)
            self._validate_output_target(job.input_file, target)
            if self._target_signature(target) != job.target_signature:
                raise OSError("result_target_changed")
            os.replace(staged, target)
            job.committed = True
            return result
        finally:
            self._safe_unlink(prepared)
            self._safe_unlink(staged)

    @staticmethod
    def _target_signature(path):
        """Detect a destination created or changed since overwrite approval."""
        try:
            stat = Path(path).stat()
            return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns
        except FileNotFoundError:
            return None

    @staticmethod
    def _validate_output_target(input_file, output_file):
        """Reject path aliases and existing hard links to the source."""
        source, target = Path(input_file), Path(output_file)
        if source.resolve() == target.resolve() or (source.exists() and target.exists() and source.samefile(target)):
            raise ValueError("result_source_target")

    def _confirm_ocr_processing(self, language):
        choices = [(get_ui_string(self.strings, key), value) for key, value in OCR_LANGUAGE_CHOICES]
        return ask_choice_ok_cancel(
            self, get_ui_string(self.strings, "ocr_prompt_title"), get_ui_string(self.strings, "ocr_prompt"),
            get_ui_string(self.strings, "ocr_language_label"), choices, language,
            details_text=get_ui_string(self.strings, "ocr_prompt_details"),
            details_show_label=get_ui_string(self.strings, "ocr_prompt_more_info_show"),
            details_hide_label=get_ui_string(self.strings, "ocr_prompt_more_info_hide"),
        )

    def _create_temp_pdf_path(self):
        temp_file = tempfile.NamedTemporaryFile(prefix="hsph-ocr-", suffix=".pdf", delete=False)
        temp_file.close()
        return temp_file.name

    def _safe_unlink(self, path):
        if path is not None:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                logging.getLogger(__name__).warning("Could not remove temporary PDF %s", path, exc_info=True)

    def _ask_output_file(self, input_file):
        while not self._closing:
            target = filedialog.asksaveasfilename(
                parent=self.root, defaultextension=".pdf", filetypes=[("PDF files", "*.pdf")],
                initialdir=str(Path(input_file).parent),
                initialfile=get_ui_string(self.strings, "file_out_pattern").format(Path(input_file).stem),
                confirmoverwrite=False,
            )
            if not target:
                return None
            try:
                self._validate_output_target(input_file, target)
            except ValueError:
                show_error(self, get_ui_string(self.strings, "error"), get_ui_string(self.strings, "result_source_target"))
                continue
            if not Path(target).exists() or ask_ok_cancel(
                self, get_ui_string(self.strings, "result_overwrite_title"),
                get_ui_string(self.strings, "result_overwrite").format(target),
            ):
                return target
        return None

    def finalize_processing(self, job):
        """Release a terminated worker while preserving completion and retry data."""
        if self._job is not job or job.result is None or (job.worker is not None and job.worker.is_alive()):
            return
        result = job.result if job.committed else ("aborted",) if job.cancel.is_set() else job.result
        job.result = result
        self.last_processing_result = result
        self._job = None
        self.processing_active = False
        if result[0] == "save_failed" and not self._closing:
            self._pending_save = job
        else:
            self._cleanup_job_result(job)
        if job.started:
            self._result_job = job
        self._set_determinate_progress()
        self.progress_bar["value"] = 0
        status = {"saved": "status_done", "aborted": "status_aborted_processing", "info_key": "val_nothing"}.get(result[0], "result_failed")
        self._set_status(status)
        self._set_inputs_locked()
        self._render_operation()
        self._render_result()
        if not self._closing and result[0] in ("failed", "error_key"):
            show_error(self, get_ui_string(self.strings, "error"), get_ui_string(
                self.strings, result[1] if result[0] == "error_key" else "result_processing_failed",
            ))

    def _cleanup_job_result(self, job):
        """Release a retained document before removing its OCR backing file."""
        if job.document is not None:
            with suppress(Exception):
                job.document.close()
            job.document = None
        self._safe_unlink(job.ocr_temp_file)
        job.ocr_temp_file = None

    def discard_pending_save(self):
        """Explicitly discard an unsaved calculation, also used during app close."""
        job = getattr(self, "_pending_save", None)
        if job is None or self._job:
            return
        self._cleanup_job_result(job)
        self._pending_save = None
        job.result = ("aborted",)
        self._set_status("status_aborted_processing")
        self._render_operation()
        self._render_result()

    def retry_save(self, choose_target=False):
        """Save retained data in a new owned worker without repeating OCR/pages."""
        job = getattr(self, "_pending_save", None)
        if job is None or self.output_options_locked:
            return
        # Reserve the UI even during a nested native file dialog.
        self._job = job
        self._set_inputs_locked()
        try:
            if choose_target:
                job.dialog_open = True
                try:
                    target = self._ask_output_file(job.input_file)
                finally:
                    job.dialog_open = False
                if not target:
                    return
                job.output_file = target
                job.target_signature = self._target_signature(target)
            if self._closing:
                return
            self._pending_save = None
            job.result = None
            job.cancel.clear()
            self.processing_active = True
            self._set_status("status_saving")
            self._start_indeterminate_progress_gui()
            job.worker = threading.Thread(target=self._retry_save_worker, args=(job,), daemon=True)
            job.worker.start()
        except Exception as exc:
            logging.getLogger(__name__).exception("Could not start or prepare PDF save retry")
            job.result = ("save_failed", str(exc))
            job.worker = None
            self.finalize_processing(job)
        finally:
            if getattr(self, "_pending_save", None) is job:
                self._job = None
                if self._closing:
                    self.discard_pending_save()
            self._set_inputs_locked()
            self._render_operation()
            self._render_result()

    def _retry_save_worker(self, job):
        """Reuse the retained calculation and report a new save outcome."""
        try:
            saved = self._save_processed_document(job, job.document, job.output_file, bool(job.ocr_temp_file), job.options)
            matches, skipped, pages, lines = job.counts
            result = ("saved", matches, skipped, saved, pages, lines, job.output_file)
        except OcrCancelled:
            result = ("aborted",)
        except Exception as exc:
            logging.getLogger(__name__).exception("Retry saving PDF failed")
            result = ("save_failed", str(exc))
        self._post_job(job, "result", *result)

    def _result_text(self):
        """Translate the retained outcome without changing its resources or state."""
        job = getattr(self, "_result_job", None)
        if job is None:
            return get_ui_string(self.strings, "result_empty")
        result = job.result
        if result is None:
            return get_ui_string(self.strings, "status_saving")
        kind = result[0]
        if kind == "saved":
            _, matches, skipped, saved, pages, lines, target = result
            text = get_ui_string(self.strings, "status_done") + "\n"
            text += self.get_plural_string("marked_lines", lines).format(lines)
            text += "\n" + self.get_plural_string("processing_complete", matches).format(matches, skipped)
            if pages:
                text += "\n" + self.get_plural_string("watermarked_pages", pages).format(pages)
            text += "\n" + get_ui_string(self.strings, "result_location").format(target)
            if saved and saved.reduction_failed:
                text += "\n" + get_ui_string(self.strings, "ocr_reduction_failed").format(saved.output_size / (1024 * 1024))
            return text
        if kind == "save_failed":
            message = get_ui_string(self.strings, "result_save_failed")
            # Only our actionable destination checks belong in the UI. Native
            # exceptions and worker diagnostics remain in the logs.
            if result[1] in ("result_target_changed", "result_source_target"):
                message = get_ui_string(self.strings, result[1]) + "\n" + message
            return message
        if kind == "info_key":
            return get_ui_string(self.strings, result[1])
        if kind == "aborted":
            return get_ui_string(self.strings, "status_aborted_processing")
        return get_ui_string(self.strings, result[1] if kind == "error_key" else "result_processing_failed")

    def _render_result(self):
        """Keep completion details and available actions in the main window."""
        if not hasattr(self, "result_label"):
            return
        self.result_frame.configure(text=get_ui_string(self.strings, "result_title"))
        self.result_label.configure(text=self._result_text())
        job = getattr(self, "_result_job", None)
        saved = bool(job and job.result and job.result[0] == "saved")
        pending = bool(getattr(self, "_pending_save", None))
        for key, button in self.result_buttons.items():
            button.configure(text=get_ui_string(self.strings, key), state="normal" if (
                not self.output_options_locked and (saved if key.startswith("result_open") else pending)
            ) else "disabled")
            if (key.startswith("result_open") and not pending) or (not key.startswith("result_open") and pending):
                button.grid()
            else:
                button.grid_remove()

        self._layout_result_actions()

    def _open_result(self, folder=False):
        """Dispatch a saved path through the system without shell interpolation."""
        job = getattr(self, "_result_job", None)
        if self.output_options_locked or not job or not job.result or job.result[0] != "saved":
            return
        target = Path(job.result[6])
        try:
            if not target.is_file():
                show_error(self, get_ui_string(self.strings, "error"), get_ui_string(self.strings, "result_open_missing"))
                return
            path = str(target.parent if folder else target)
            if os.name == "nt":
                os.startfile(path)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
        except Exception:
            logging.getLogger(__name__).exception("Could not open saved PDF or folder: %s", target)
            show_error(self, get_ui_string(self.strings, "error"), get_ui_string(self.strings, "result_open_failed"))

    def open_result_pdf(self):
        """Open the last successfully saved PDF with the system application."""
        self._open_result()

    def open_result_folder(self):
        """Open the saved PDF's containing folder."""
        self._open_result(folder=True)

    def _start_indeterminate_progress_gui(self):
        self.progress_bar.stop()
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar["maximum"] = INDETERMINATE_PROGRESS_MAXIMUM
        self.progress_bar["value"] = 0
        self.progress_bar.start(INDETERMINATE_PROGRESS_INTERVAL_MS)

    def _set_determinate_progress(self):
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_bar["maximum"] = DETERMINATE_PROGRESS_MAXIMUM

    def check_for_app_updates(self, current_version: Version = Version.from_str(VERSION_STR), force_check: bool = False):
        """Check for application updates."""
        return self.update_checker.check_for_app_updates(current_version, force_check)

    def start_download(self):
        """Reserve shared controls on Tk before the downloader does any work."""
        if self.output_options_locked:
            return False
        self._download_id += 1
        self._download_cancel.clear()
        self.download_active = True
        self._set_determinate_progress()
        self.progress_bar["value"] = 0
        self._set_status("upd_starting")
        self._set_inputs_locked()
        self._render_operation()
        return True

    def abort_download(self):
        """Keep controls reserved until the downloader finishes cleanup."""
        if self.download_active:
            self._download_cancel.set()
            self._set_status("status_cancelling")
            self._render_operation()

    def finish_download(self):
        """Release shared controls after verification and resource cleanup."""
        self.download_active = False
        self._set_determinate_progress()
        self.progress_bar["value"] = 0
        self._set_status("upd_cancelled" if self._download_cancel.is_set() else "status_waiting")
        self._set_inputs_locked()
        self._render_operation()

    def is_download_aborted(self):
        return self._download_cancel.is_set() or self._closing
