"""
Main application window
"""

from contextlib import suppress
import re
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
from ..core.pdf_processor import highlight_matching_data
from ..core.watermark import watermark_pdf_page
from ..models import HighlightMode
from ..utils.localization import setup_translation
from ..utils.theme import ThemeColors, get_effective_theme, get_theme_colors, set_windows_title_bar_theme
from ..utils.updater import UpdateChecker
from ..version import Version
from .dev_tools import DevToolsWindow
from .dialogs import FilterDialog, UpdateDialogs, WatermarkDialog
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

        # Initialize the UI components
        self.setup_ui()

        # Processing and download state
        self.processing_active = False
        self.download_active = False
        self.ocr_detection_path: str | None = None
        self.ocr_detection_result: bool | None = None

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
        lang_var = StringVar()
        lang_var.set(self.app_settings.settings["language"])
        lang_var.trace_add("write", lambda *args: self.app_settings.update_setting("language", lang_var.get()))

        self.language_menu = ttk.OptionMenu(
            self.header_controls_frame,
            lang_var,
            self.app_settings.settings["language"],
            *LANGUAGE_OPTIONS,
            command=lambda _: self.on_language_change(lang_var.get()),
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
            "write", lambda *args: self.app_settings.update_setting("mark_only_relevant_lines", self.relevant_lines_var.get())
        )

        self.checkbox_relevant_lines = ttk.Checkbutton(
            self.root, text=get_ui_string(self.strings, "mark_only_relevant"), variable=self.relevant_lines_var
        )
        self.checkbox_relevant_lines.grid(row=3, column=0, columnspan=2, sticky="W", padx=10, pady=2)

        # Filter variables
        self.names_var = StringVar()
        self.names_var.set(self.app_settings.settings.get("names", ""))
        self.names_var.trace_add("write", lambda *args: self.app_settings.update_setting("names", self.names_var.get()))

        self.highlight_mode_var = StringVar()
        self.highlight_mode_var.set(self.app_settings.settings.get("highlight_mode", HighlightMode.NAMES_DIFF_COLOR.name))
        self.highlight_mode_var.trace_add("write", lambda *args: self.app_settings.update_setting("highlight_mode", self.highlight_mode_var.get()))

        self.enable_filter_var = IntVar()
        self.enable_filter_var.set(self.app_settings.settings.get("enable_filter", 0))
        self.enable_filter_var.trace_add("write", lambda *args: self.app_settings.update_setting("enable_filter", self.enable_filter_var.get()))

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
        self.on_language_change(self.app_settings.settings["language"])

    def update_all_widget_texts(self):
        """
        Update all widget texts from self.strings. Call after changing language or initializing UI.
        """
        self.root.title(get_ui_string(self.strings, "title"))
        self.title.config(text=get_ui_string(self.strings, "title"))
        self.label_pdf_file.config(text=get_ui_string(self.strings, "pdf_file"))
        self.label_search_str.config(text=get_ui_string(self.strings, "search_term"))
        self.checkbox_relevant_lines.config(text=get_ui_string(self.strings, "mark_only_relevant"))
        self.start_abort_button.config(text=get_ui_string(self.strings, "btn_start"))
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
            except Exception as e:
                logging.getLogger("main_window").debug("Could not theme %s window: %s", owner_name, e)

    def _on_theme_mode_change(self, mode: str) -> None:
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
        self.filter_dialog.open()

    def close(self) -> None:
        """Close owned windows before destroying the main application window."""
        try:
            self.preview_window_handler.close()
        except Exception as e:
            logging.getLogger("main_window").debug("Could not close preview window: %s", e)

        for owner_name in ("filter_dialog", "watermark_dialog", "dev_tools"):
            owner = getattr(self, owner_name, None)
            window = getattr(owner, "window", None)
            if window is None:
                continue
            try:
                if window.winfo_exists():
                    window.destroy()
            except Exception as e:
                logging.getLogger("main_window").debug("Could not close %s window: %s", owner_name, e)

        try:
            self.root.destroy()
        except Exception as e:
            logging.getLogger("main_window").debug("Could not destroy root window: %s", e)

    def open_watermark_window(self):
        """Open the watermark configuration dialog."""
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
        if os.getenv("HSPH_SCREENSHOT_MODE") in ("1", "true", "True"):
            self.status_var.set(get_ui_string(self.strings, "status_waiting"))
        else:
            self.status_var.set(get_ui_string(self.strings, "status_language_changed"))
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
        self.status_var.set(get_ui_string(self.strings, "status_importing"))
        self.root.update_idletasks()
        file_path = filedialog.askopenfilename(
            filetypes=((get_ui_string(self.strings, "file_filter_pdf"), "*.pdf"), (get_ui_string(self.strings, "file_filter_all"), "*.*"))
        )
        if file_path:
            file_name = Path(file_path).name
            self.pdf_file_var.set(file_name)  # Display only the file name
            self.input_file_full_path = file_path  # Store full path for processing
            self.ocr_detection_path = file_path
            self.ocr_detection_result = None
            self.status_var.set(get_ui_string(self.strings, "status_imported"))
            if self.app_settings.settings.get("ocr_enabled", "True") == "True":
                threading.Thread(target=self._detect_ocr_requirement, args=(file_path,), daemon=True).start()
        else:
            self.status_var.set(get_ui_string(self.strings, "status_waiting"))
            self.root.update_idletasks()

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
        if getattr(self, "input_file_full_path", None) == file_path and not self.processing_active:
            self.status_var.set(get_ui_string(self.strings, "status_ocr_checking"))

    def _apply_ocr_detection_result(self, file_path: str, needs_ocr: bool) -> None:
        if getattr(self, "input_file_full_path", None) != file_path:
            return
        self.ocr_detection_path = file_path
        self.ocr_detection_result = needs_ocr
        if self.processing_active:
            return
        status_key = "status_ocr_needed" if needs_ocr else "status_imported"
        self.status_var.set(get_ui_string(self.strings, status_key))

    def start_processing(self):
        """
        Starts the PDF processing based on the selected file and search parameters.
        """
        self.start_abort_button.config(text=get_ui_string(self.strings, "btn_abort"), command=self.finalize_processing)

        # Set processing flag to True
        self.processing_active = True

        # Update the search string in the settings
        if self.search_phrase_var.get() != self.app_settings.settings["search_str"]:
            self.app_settings.update_setting("search_str", self.search_phrase_var.get())

        # Start the processing in a separate thread
        input_file = getattr(self, "input_file_full_path", None)
        search_str = self.search_phrase_var.get()

        if not all([input_file, search_str]):
            show_error(self, get_ui_string(self.strings, "error"), get_ui_string(self.strings, "val_all_required"))
            self.finalize_processing()
            return

        threading.Thread(target=self.process_pdf, args=(input_file,), daemon=True).start()

    def process_pdf(self, input_file: str):
        """Process the PDF file with highlighting and watermarks."""
        self.processing_active = True
        search_str = self.search_phrase_var.get()
        only_relevant = bool(self.relevant_lines_var.get())
        filter_enabled = bool(self.enable_filter_var.get())
        highlight_mode = HighlightMode[self.highlight_mode_var.get()]
        names = [name.strip() for name in re.split(r",\s*", self.names_var.get()) if name.strip()]
        ocr_used = False
        ocr_temp_file: str | None = None
        prepared_save_temp_file: str | None = None
        document = None

        try:
            self.paths.is_valid_path(input_file)
            document = Document(input_file)
            if document.is_encrypted:
                self.root.after_idle(lambda: show_error(self, get_ui_string(self.strings, "error"), get_ui_string(self.strings, "val_pdf_protected")))
                document.close()
                document = None
                return

            if self._should_use_ocr(input_file, document):
                language = self._confirm_ocr_processing_threadsafe()
                if language is None:
                    document.close()
                    document = None
                    self.root.after_idle(lambda: self.status_var.set(get_ui_string(self.strings, "status_aborted")))
                    return
                if language != self.app_settings.settings.get("ocr_language"):
                    self.app_settings.update_setting("ocr_language", language)

                try:
                    tessdata_dir = ensure_bundled_tessdata(self.paths.ocr_tessdata_dir, language)
                except FileNotFoundError:
                    document.close()
                    document = None
                    self.root.after_idle(
                        lambda: show_error(self, get_ui_string(self.strings, "error"), get_ui_string(self.strings, "val_ocr_assets_missing"))
                    )
                    return

                dpi = int(self.app_settings.settings.get("ocr_dpi", 300))
                ocr_temp_file = self._create_temp_pdf_path()
                document.close()
                document = None
                create_searchable_ocr_pdf_in_process(
                    input_file,
                    ocr_temp_file,
                    tessdata_dir=tessdata_dir,
                    language=language,
                    dpi=dpi,
                    progress_callback=self.update_ocr_progress,
                    is_cancelled=lambda: not self.processing_active,
                )
                document = Document(ocr_temp_file)
                ocr_used = True

            total_matches = 0
            total_skipped = 0
            total_pages = len(document)

            for i in range(len(document)):
                page = document[i]

                if not self.processing_active:  # Check if the process should continue
                    document.close()
                    document = None
                    self._safe_unlink(ocr_temp_file)
                    ocr_temp_file = None
                    self.root.after_idle(lambda: self.status_var.set(get_ui_string(self.strings, "status_aborted")))
                    self.root.after_idle(self.finalize_processing)
                    return

                # Highlight the matching data on the page
                matches_found, skipped_matches = highlight_matching_data(
                    page=page,
                    search_str=search_str,
                    only_relevant=only_relevant,
                    filter_enabled=filter_enabled,
                    names=names,
                    highlight_mode=highlight_mode,
                )
                total_matches += matches_found
                total_skipped += skipped_matches

                # Apply watermark on each page if enabled
                watermark_pdf_page(page, self.app_settings.settings)

                # Update the progress bar and status message
                self.update_progress(i + 1, total_pages, total_matches, total_skipped)

            total_marked = total_matches - total_skipped

            if self.processing_active and total_marked > 0:  # Check we finished normally and have matches
                output_file = self._ask_output_file_threadsafe(input_file)
                if output_file and self.processing_active:
                    self.start_indeterminate_progress(get_ui_string(self.strings, "status_saving"))
                    save_result = None
                    if ocr_used:
                        prepared_save_temp_file = self._create_temp_pdf_path()
                        document.save(prepared_save_temp_file)
                        document.close()
                        document = None
                        save_result = save_pdf_path_in_process(
                            prepared_save_temp_file,
                            output_file,
                            original_pdf_path=input_file,
                            ocr_used=True,
                            reduce_large_outputs=self.app_settings.settings.get("ocr_reduce_large_outputs", "True") == "True",
                            is_cancelled=lambda: not self.processing_active,
                        )
                        self._safe_unlink(prepared_save_temp_file)
                        prepared_save_temp_file = None
                    else:
                        save_compact_pdf(document, output_file)
                        document.close()
                        document = None
                    self._safe_unlink(ocr_temp_file)
                    ocr_temp_file = None

                    message = self.get_plural_string("processing_complete", total_matches).format(total_matches, total_skipped)
                    if save_result and save_result.reduction_failed:
                        message += "\n\n" + get_ui_string(self.strings, "ocr_reduction_failed").format(save_result.output_size / (1024 * 1024))
                    self.root.after_idle(lambda: show_info(self, get_ui_string(self.strings, "status_done"), message))
                elif output_file:
                    document.close()
                    document = None
                    self._safe_unlink(ocr_temp_file)
                    ocr_temp_file = None
                    self.root.after_idle(lambda: self.status_var.set(get_ui_string(self.strings, "status_aborted")))
                else:
                    document.close()
                    document = None
                    self._safe_unlink(ocr_temp_file)
                    ocr_temp_file = None
                    self.root.after_idle(lambda: show_info(self, get_ui_string(self.strings, "info"), get_ui_string(self.strings, "val_no_output")))
            elif self.processing_active and total_marked == 0:
                document.close()
                document = None
                self._safe_unlink(ocr_temp_file)
                ocr_temp_file = None
                self.root.after_idle(lambda: show_info(self, get_ui_string(self.strings, "info"), get_ui_string(self.strings, "val_nothing")))

        except OcrCancelled:
            self.root.after_idle(lambda: self.status_var.set(get_ui_string(self.strings, "status_aborted")))
        except Exception as e:
            error_msg = str(e)
            self.root.after_idle(lambda: show_error(self, get_ui_string(self.strings, "error"), error_msg))
        finally:
            if document is not None:
                with suppress(Exception):
                    document.close()
            self._safe_unlink(ocr_temp_file)
            self._safe_unlink(prepared_save_temp_file)
            self.root.after_idle(self.finalize_processing)

    def _should_use_ocr(self, input_file: str, document: Document) -> bool:
        if self.app_settings.settings.get("ocr_enabled", "True") != "True":
            return False
        if self.ocr_detection_path == input_file and self.ocr_detection_result is not None:
            return self.ocr_detection_result
        return document_needs_ocr(document)

    def _confirm_ocr_processing(self) -> str | None:
        choices = [(get_ui_string(self.strings, label_key), value) for label_key, value in OCR_LANGUAGE_CHOICES]
        return ask_choice_ok_cancel(
            self,
            get_ui_string(self.strings, "ocr_prompt_title"),
            get_ui_string(self.strings, "ocr_prompt"),
            get_ui_string(self.strings, "ocr_language_label"),
            choices,
            self.app_settings.settings.get("ocr_language", "deu"),
            details_text=get_ui_string(self.strings, "ocr_prompt_details"),
            details_show_label=get_ui_string(self.strings, "ocr_prompt_more_info_show"),
            details_hide_label=get_ui_string(self.strings, "ocr_prompt_more_info_hide"),
        )

    def _confirm_ocr_processing_threadsafe(self) -> str | None:
        if threading.current_thread() is threading.main_thread():
            return self._confirm_ocr_processing()

        results: queue.Queue[str | None | Exception] = queue.Queue(maxsize=1)

        def ask_on_main_thread() -> None:
            try:
                results.put(self._confirm_ocr_processing())
            except Exception as exc:
                results.put(exc)

        self.root.after(0, ask_on_main_thread)
        result = results.get()
        if isinstance(result, Exception):
            raise result
        return result

    def _create_temp_pdf_path(self) -> str:
        temp_file = tempfile.NamedTemporaryFile(prefix="hsph-ocr-", suffix=".pdf", delete=False)
        temp_file.close()
        return temp_file.name

    def _safe_unlink(self, path: str | Path | None) -> None:
        if path is None:
            return
        with suppress(OSError):
            Path(path).unlink()

    def _ask_output_file_threadsafe(self, input_file: str) -> str:
        if threading.current_thread() is threading.main_thread():
            return self._ask_output_file(input_file)

        results: queue.Queue[str | Exception] = queue.Queue(maxsize=1)

        def ask_on_main_thread() -> None:
            try:
                results.put(self._ask_output_file(input_file))
            except Exception as exc:
                results.put(exc)

        self.root.after(0, ask_on_main_thread)
        result = results.get()
        if isinstance(result, Exception):
            raise result
        return result

    def _ask_output_file(self, input_file: str) -> str:
        return filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile=get_ui_string(self.strings, "file_out_pattern").format(input_file.rsplit(".", 1)[0]),
        )

    def finalize_processing(self):
        """
        Resets the UI to the initial state, regardless of whether processing was completed or aborted.
        """
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_bar["maximum"] = DETERMINATE_PROGRESS_MAXIMUM
        self.progress_bar["value"] = 0  # Reset progress bar
        # Reset the button to "Start" with the original command
        self.start_abort_button.config(text=get_ui_string(self.strings, "btn_start"), command=self.start_processing)
        if not self.processing_active:  # Only update the status if the processing was aborted
            self.status_var.set(get_ui_string(self.strings, "status_aborted_processing"))
        else:
            self.status_var.set(get_ui_string(self.strings, "status_waiting"))
        self.processing_active = False  # Reset the flag
        self.root.update_idletasks()  # Ensure the UI updates are processed

    def update_progress(self, current: int, total: int, matches: int, skipped: int):
        """
        Updates the progress bar and status message.

        Args:
            current (int): The current page being processed.
            total (int): The total number of pages in the PDF.
            matches (int): The total number of matches found.
            skipped (int): The total number of matches skipped.
        """
        # Schedule GUI updates on the main thread
        self.root.after_idle(lambda: self._update_progress_gui(current, total, matches, skipped))

    def _update_progress_gui(self, current: int, total: int, matches: int, skipped: int):
        """Internal method to update progress GUI elements on main thread."""
        self._set_determinate_progress()
        self.progress_bar["value"] = (current / total) * 100
        self.status_var.set(self.get_plural_string("processed_pages", matches).format(current, total, matches, skipped))

    def update_ocr_progress(self, current: int, total: int):
        """Schedule OCR progress updates on the main thread."""
        self.root.after_idle(lambda: self._update_ocr_progress_gui(current, total))

    def _update_ocr_progress_gui(self, current: int, total: int):
        self._set_determinate_progress()
        if total > 0:
            self.progress_bar["value"] = (current / total) * 100
        self.status_var.set(get_ui_string(self.strings, "status_ocr_processing").format(current, total))

    def start_indeterminate_progress(self, status_text: str) -> None:
        """Show an animated progress bar while an operation cannot report exact progress."""
        if threading.current_thread() is threading.main_thread():
            self._start_indeterminate_progress_gui(status_text)
            return

        started = threading.Event()

        def start_on_main_thread() -> None:
            try:
                self._start_indeterminate_progress_gui(status_text)
            finally:
                started.set()

        self.root.after(0, start_on_main_thread)
        started.wait(timeout=1.0)

    def _start_indeterminate_progress_gui(self, status_text: str) -> None:
        self.progress_bar.stop()
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar["maximum"] = INDETERMINATE_PROGRESS_MAXIMUM
        self.progress_bar["value"] = 0
        self.progress_bar.start(INDETERMINATE_PROGRESS_INTERVAL_MS)
        self.status_var.set(status_text)
        self.root.update_idletasks()

    def _set_determinate_progress(self) -> None:
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_bar["maximum"] = DETERMINATE_PROGRESS_MAXIMUM

    def check_for_app_updates(self, current_version: Version = Version.from_str(VERSION_STR), force_check: bool = False):
        """Check for application updates."""
        return self.update_checker.check_for_app_updates(current_version, force_check)

    def start_download(self):
        """Start download process and update UI."""
        self.download_active = True
        self.start_abort_button.config(text=get_ui_string(self.strings, "btn_abort"), command=self.abort_download)
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_bar["maximum"] = DETERMINATE_PROGRESS_MAXIMUM
        self.progress_bar["value"] = 0  # Reset progress bar

    def abort_download(self):
        """Abort download process and reset UI."""
        self.download_active = False
        self.start_abort_button.config(text=get_ui_string(self.strings, "btn_start"), command=self.start_processing)
        self.progress_bar.stop()
        self.progress_bar.configure(mode="determinate")
        self.progress_bar["maximum"] = DETERMINATE_PROGRESS_MAXIMUM
        self.progress_bar["value"] = 0  # Reset progress bar
        self.status_var.set(get_ui_string(self.strings, "upd_cancelled"))

    def finish_download(self):
        """Finish download process and reset UI."""
        self.download_active = False
        # Don't reset button here since app will close after download

    def is_download_aborted(self):
        """Check if download has been aborted."""
        return not self.download_active
