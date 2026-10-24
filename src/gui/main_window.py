"""
Main application window
"""

from contextlib import suppress
from collections.abc import Callable
from dataclasses import dataclass, field, replace
import os
import logging
import queue
import threading
import tempfile
import time
from pathlib import Path
from tkinter import Button, IntVar, Menu, StringVar, TclError, Tk, filedialog, ttk

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
from .message_dialog import ask_choice_ok_cancel, show_error, show_info
from .preview import PreviewWindow
from .ui_strings import _xgettext_dummy, build_strings, get_ui_string, plural_strings
from .widgets import Tooltip

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
        self._next_job_id = 0
        self._ui_messages = queue.Queue()
        self._closing = False
        self._download_cancel = threading.Event()
        self._download_id = 0
        self.processing_active = False
        self.download_active = False
        self._status = ("status_waiting", ())

        # Initialize the UI components
        self._syncing_settings = True
        self.setup_ui()
        self._syncing_settings = False

        # Cached detection belongs to the currently selected source.
        self.ocr_detection_path: str | None = None
        self.ocr_detection_result: bool | None = None
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
        self.root.geometry("600x350")  # Adjusted size for a better layout
        self.root.minsize(width=600, height=350)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        # Improved styling
        self.style = ttk.Style()
        self.style.configure("TButton", font=("Arial", 10), borderwidth="4")  # Button styling
        self.style.configure("TMenubutton", font=("Arial", 10), borderwidth="4")  # Theme selector styling
        self.style.configure("TEntry", font=("Arial", 10), borderwidth="2")  # Entry widget styling
        self.style.configure("TCheckbutton", font=("Arial", 10))  # Checkbutton styling
        self.style.configure("Horizontal.TProgressbar", thickness=20)  # Progressbar styling
        self._apply_theme()
        self.theme_icon_images = self._load_theme_icon_images()

        # Add icon
        self.root.iconbitmap(self.paths.icon_path)

        self.header_frame = ttk.Frame(self.root)
        self.header_frame.grid(row=0, column=0, columnspan=3, sticky="EW")
        self.header_frame.grid_columnconfigure(1, weight=1)

        # Load and display the logo
        logo_image = Image.open(self.paths.logo_path)
        title_font = ("Arial", 16, "bold")
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
        self.title.grid(row=0, column=1, sticky="W", padx=(14, 10), pady=10)

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

        # PDF file selection
        self.label_pdf_file = ttk.Label(self.root, text=get_ui_string(self.strings, "pdf_file"))
        self.label_pdf_file.grid(row=1, column=0, sticky="E", padx=(10, 4), pady=2)
        self.pdf_file_var = StringVar()

        self.entry_file = ttk.Entry(self.root, textvariable=self.pdf_file_var, state="readonly")
        self.entry_file.grid(row=1, column=1, sticky="WE", padx=(2, 0))

        self.browse_button = ttk.Button(
            self.root,
            text=get_ui_string(self.strings, "btn_browse"),
            command=self.browse_file,
            width=UNIFORM_ACTION_BUTTON_WIDTH,
        )
        self.browse_button.grid(row=1, column=2, sticky="E", padx=(3, 10))

        # Search string
        self.label_search_str = ttk.Label(self.root, text=get_ui_string(self.strings, "search_term"))
        self.label_search_str.grid(row=2, column=0, sticky="E", padx=(10, 4), pady=2)

        self.search_phrase_var = StringVar()
        self.search_phrase_var.set(self.app_settings.settings["search_str"])

        self.entry_search_str = ttk.Entry(self.root, textvariable=self.search_phrase_var)
        self.entry_search_str.grid(row=2, column=1, sticky="WE", padx=(2, 0))

        # Options for highlighting
        self.relevant_lines_var = IntVar()
        self.relevant_lines_var.set(self.app_settings.settings.get("mark_only_relevant_lines", 1))
        self.relevant_lines_var.trace_add(
            "write", lambda *args: self._persist_setting("mark_only_relevant_lines", self.relevant_lines_var.get())
        )

        self.checkbox_relevant_lines = ttk.Checkbutton(
            self.root, text=get_ui_string(self.strings, "mark_only_relevant"), variable=self.relevant_lines_var
        )
        self.checkbox_relevant_lines.grid(row=3, column=0, columnspan=2, sticky="W", padx=10, pady=2)

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
        self.button_filter = ttk.Button(
            self.root,
            text=get_ui_string(self.strings, "btn_filter"),
            command=self.open_filter_window,
            width=UNIFORM_ACTION_BUTTON_WIDTH,
        )
        self.button_filter.grid(row=3, column=1, sticky="E", padx=(2, 1), pady=2)

        self.button_watermark = ttk.Button(
            self.root,
            text=get_ui_string(self.strings, "btn_watermark"),
            command=self.open_watermark_window,
            width=UNIFORM_ACTION_BUTTON_WIDTH,
        )
        self.button_watermark.grid(row=3, column=2, sticky="E", padx=(3, 10), pady=2)

        # Progress bar
        self.progress_bar = ttk.Progressbar(self.root, orient="horizontal", length=400, mode="determinate")
        self.progress_bar.grid(row=4, column=0, columnspan=3, padx=10, pady=20, sticky="WE")

        # Status label
        self.status_var = StringVar()
        self.status_var.set(get_ui_string(self.strings, "status_waiting"))
        self.status_label = ttk.Label(self.root, textvariable=self.status_var)
        self.status_label.grid(row=5, column=0, columnspan=3, padx=10, pady=2)

        # Start/Abort button
        self.start_abort_button = ttk.Button(self.root, text=get_ui_string(self.strings, "btn_start"), command=self.start_processing)
        self.start_abort_button.grid(row=6, column=1, pady=10)

        # Version frame
        self.version_frame = ttk.Frame(self.root)
        self.version_frame.grid(row=7, column=0, columnspan=3, padx=10, pady=2, sticky="WE")

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

        # Make version frame sticky to the bottom
        self.root.grid_rowconfigure(7, weight=1)

        # Set up the grid layout
        self.root.grid_columnconfigure(0, minsize=178)  # Set a fixed minimum width for column 0
        self.root.grid_columnconfigure(1, weight=1)  # Allow column 1 to expand and fill space
        self.root.grid_columnconfigure(2, weight=0)  # Header controls and action buttons

        self._apply_theme()
        self.update_all_widget_texts()
        self._poll_system_theme()
        # Set the initial state of the UI based on the settings
        self.sync_settings_to_ui()

    def _persist_setting(self, key, value):
        if not getattr(self, "_syncing_settings", False):
            self.app_settings.update_setting(key, value)

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
        Tooltip(self.entry_file, text=get_ui_string(self.strings, "select_pdf"))
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
        label_font = ("Arial", 10)
        title_font = ("Arial", 16, "bold")

        self.style.configure(".", background=colors.background, foreground=colors.foreground)
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

        if widget_class in {"Tk", "Toplevel", "Frame", "Labelframe"}:
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
        if not self.output_options_locked:
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
            state="disabled" if cancelling or self._closing else "normal",
        )
        key, args = self._status
        self._set_status(key, *args)

    def start_processing(self):
        """Validate and capture one job before starting its owned worker."""
        if self.output_options_locked or getattr(self.update_checker, "_active_download", False):
            return
        input_file = getattr(self, "input_file_full_path", None)
        options = self._collect_processing_options()
        if not input_file or not options.search_str:
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
            if kind == "result":
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
            if options.ocr_enabled and (needs_ocr if needs_ocr is not None else document_needs_ocr(document)):
                def confirm():
                    language = self._confirm_ocr_processing(options.ocr_language)
                    if language is not None and not job.cancel.is_set() and not self._closing:
                        self.app_settings.update_setting("ocr_language", language)
                        job.options = replace(options, ocr_language=language)
                        return job.options
                    return None
                options = self._request_job_ui(job, confirm)
                if options is None:
                    raise OcrCancelled()
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
            total_matches, total_skipped, watermark_pages = self._process_document_pages(job, document, options)
            self._raise_if_cancelled(job)
            if total_matches - total_skipped <= 0 and watermark_pages == 0:
                outcome = ("info_key", "val_nothing")
                return
            output_file = self._request_job_ui(job, lambda: self._ask_output_file(job.input_file))
            if not output_file:
                outcome = ("info_key", "val_no_output")
                return
            self._raise_if_cancelled(job)
            self._post_job(job, "phase", "saving", "status_saving")
            save_result = self._save_processed_document(job, document, output_file, bool(ocr_temp_file), options)
            self._raise_if_cancelled(job)
            outcome = ("saved", total_matches, total_skipped, save_result, watermark_pages)
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
        total_matches = total_skipped = watermark_pages = 0
        watermark = dict(options.watermark)
        for index in range(len(document)):
            self._raise_if_cancelled(job)
            matches, skipped = highlight_matching_data(
                page=document[index], search_str=options.search_str, only_relevant=options.only_relevant,
                names=options.names, marking_mode=options.marking_mode,
                highlight_color_default=options.highlight_color_default, highlight_color_names=options.highlight_color_names,
            )
            total_matches += matches
            total_skipped += skipped
            self._raise_if_cancelled(job)
            if watermark_pdf_page(document[index], watermark):
                watermark_pages += 1
            self._post_job(job, "pages", index + 1, len(document), total_matches, total_skipped)
        return total_matches, total_skipped, watermark_pages

    def _save_processed_document(self, job, document, output_file, ocr_used, options):
        self._raise_if_cancelled(job)
        if not ocr_used:
            save_compact_pdf(document, output_file)
            self._raise_if_cancelled(job)
            return None
        prepared = self._create_temp_pdf_path()
        try:
            document.save(prepared)
            self._raise_if_cancelled(job)
            document.close()
            return save_pdf_path_in_process(
                prepared, output_file, original_pdf_path=job.input_file, ocr_used=True,
                reduce_large_outputs=options.ocr_reduce_large_outputs, is_cancelled=job.cancel.is_set,
            )
        finally:
            self._safe_unlink(prepared)

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
            with suppress(OSError):
                Path(path).unlink()

    def _ask_output_file(self, input_file):
        return filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".pdf", filetypes=[("PDF files", "*.pdf")],
            initialfile=get_ui_string(self.strings, "file_out_pattern").format(input_file.rsplit(".", 1)[0]),
        )

    def finalize_processing(self, job):
        """Release a terminated job, then present only its own terminal result."""
        if self._job is not job or job.result is None or (job.worker is not None and job.worker.is_alive()):
            return
        result = ("aborted",) if job.cancel.is_set() else job.result
        self.last_processing_result = result
        self._job = None
        self.processing_active = False
        self._set_determinate_progress()
        self.progress_bar["value"] = 0
        self._set_status("status_aborted_processing" if result[0] == "aborted" else "status_waiting")
        self._set_inputs_locked()
        self._render_operation()
        if self._closing:
            return
        if result[0] == "saved":
            _, matches, skipped, save_result, watermark_pages = result
            message = self.get_plural_string("processing_complete", matches).format(matches, skipped)
            if watermark_pages:
                message += "\n" + self.get_plural_string("watermarked_pages", watermark_pages).format(watermark_pages)
            if save_result and save_result.reduction_failed:
                message += "\n\n" + get_ui_string(self.strings, "ocr_reduction_failed").format(save_result.output_size / (1024 * 1024))
            show_info(self, get_ui_string(self.strings, "status_done"), message)
        elif result[0] == "info_key":
            show_info(self, get_ui_string(self.strings, "info"), get_ui_string(self.strings, result[1]))
        elif result[0] in ("failed", "error_key"):
            show_error(self, get_ui_string(self.strings, "error"), get_ui_string(self.strings, result[1]) if result[0] == "error_key" else result[1])

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
