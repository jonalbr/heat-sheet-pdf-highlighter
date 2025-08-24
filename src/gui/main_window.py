"""
Main application window
"""

import re
import threading
import time
from pathlib import Path
from tkinter import IntVar, Label, StringVar, Tk, filedialog, messagebox, ttk

from PIL import Image, ImageTk
from pymupdf import Document

from ..config.paths import Paths
from ..config.settings import AppSettings
from ..constants import LANGUAGE_OPTIONS, VERSION_STR
from ..core.pdf_processor import highlight_matching_data
from ..core.watermark import watermark_pdf_page
from ..models import HighlightMode
from ..utils.localization import setup_translation
from ..utils.updater import UpdateChecker
from ..version import Version
from .dev_tools import DevToolsWindow
from .dialogs import FilterDialog, UpdateDialogs, WatermarkDialog
from .preview import PreviewWindow
from .ui_strings import _xgettext_dummy, build_strings, get_ui_string, plural_strings
from .widgets import Tooltip


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

        # Check for updates AFTER UI is set up
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

        # Improved styling
        style = ttk.Style()
        style.theme_use("Breeze")  # A more modern theme than the default
        style.configure("TButton", font=("Arial", 10), borderwidth="4")  # Button styling
        style.configure("TLabel", font=("Arial", 10), background="#f0f0f0")  # Label styling
        style.configure("TEntry", font=("Arial", 10), borderwidth="2")  # Entry widget styling
        style.configure("TCheckbutton", font=("Arial", 10))  # Checkbutton styling
        style.configure("Horizontal.TProgressbar", thickness=20)  # Progressbar styling

        # Background color
        self.root.configure(background="#f0f0f0")

        # Add icon
        self.root.iconbitmap(self.paths.icon_path)

        # Load and display the logo
        logo_image = Image.open(self.paths.logo_path)
        title_font = ("Arial", 16, "bold")
        title_height = 50  # Set the desired height of the title
        logo_image = logo_image.resize((title_height, title_height))
        logo_photo = ImageTk.PhotoImage(logo_image)
        self.logo_label = Label(self.root, image=logo_photo)
        setattr(self.logo_label, "image", logo_photo)  # Store a reference to the image
        self.logo_label.grid(row=0, column=0, sticky="W", padx=10, pady=10)

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
        self.title = ttk.Label(self.root, text=get_ui_string(self.strings, "title"), font=title_font)
        self.title.grid(row=0, column=1, sticky="EW", padx=10, pady=10)

        # Language selection
        lang_var = StringVar()
        lang_var.set(self.app_settings.settings["language"])
        lang_var.trace_add("write", lambda *args: self.app_settings.update_setting("language", lang_var.get()))

        self.language_menu = ttk.OptionMenu(
            self.root, lang_var, self.app_settings.settings["language"], *LANGUAGE_OPTIONS, command=lambda _: self.on_language_change(lang_var.get())
        )
        self.language_menu.grid(row=0, column=2, sticky="E", padx=10, pady=10)

        # PDF file selection
        self.label_pdf_file = ttk.Label(self.root, text=get_ui_string(self.strings, "pdf_file"))
        self.label_pdf_file.grid(row=1, column=0, sticky="E", padx=10, pady=2)
        self.pdf_file_var = StringVar()
        self.entry_file = ttk.Entry(self.root, textvariable=self.pdf_file_var, state="readonly")
        self.entry_file.grid(row=1, column=1, sticky="WE", padx=10)

        self.browse_button = ttk.Button(self.root, text=get_ui_string(self.strings, "btn_browse"), command=self.browse_file, width=11)
        self.browse_button.grid(row=1, column=2, padx=10, sticky="E")

        # Search string
        self.label_search_str = ttk.Label(self.root, text=get_ui_string(self.strings, "search_term"))
        self.label_search_str.grid(row=2, column=0, sticky="E", padx=10, pady=2)

        self.search_phrase_var = StringVar()
        self.search_phrase_var.set(self.app_settings.settings["search_str"])

        self.entry_search_str = ttk.Entry(self.root, textvariable=self.search_phrase_var)
        self.entry_search_str.grid(row=2, column=1, sticky="WE", columnspan=2, padx=10)

        # Filter frame
        self.filter_frame = ttk.Frame(self.root)
        self.filter_frame.grid(row=3, column=0, columnspan=3, sticky="WE", pady=2)
        self.filter_frame.grid_columnconfigure(1, weight=1)

        # Options for highlighting
        self.relevant_lines_var = IntVar()
        self.relevant_lines_var.set(self.app_settings.settings.get("mark_only_relevant_lines", 1))
        self.relevant_lines_var.trace_add(
            "write", lambda *args: self.app_settings.update_setting("mark_only_relevant_lines", self.relevant_lines_var.get())
        )

        self.checkbox_relevant_lines = ttk.Checkbutton(
            self.filter_frame, text=get_ui_string(self.strings, "mark_only_relevant"), variable=self.relevant_lines_var
        )
        self.checkbox_relevant_lines.grid(row=0, column=0, sticky="W", padx=10)

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
        self.button_filter = ttk.Button(self.filter_frame, text=get_ui_string(self.strings, "btn_filter"), command=self.open_filter_window)
        self.button_filter.grid(row=0, column=1, sticky="E", padx=10)

        self.button_watermark = ttk.Button(self.filter_frame, text=get_ui_string(self.strings, "btn_watermark"), command=self.open_watermark_window)
        self.button_watermark.grid(row=0, column=2, sticky="E", padx=10)

        # Progress bar
        self.progress_bar = ttk.Progressbar(self.root, orient="horizontal", length=400, mode="determinate")
        self.progress_bar.grid(row=4, column=0, columnspan=3, padx=10, pady=20, sticky="WE")

        # Status label
        self.status_var = StringVar()
        self.status_var.set(get_ui_string(self.strings, "status_waiting"))
        ttk.Label(self.root, textvariable=self.status_var).grid(row=5, column=0, columnspan=3, padx=10, pady=2)

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

        self.update_label = ttk.Label(self.version_frame, text="...", foreground="#5c5c5c", cursor="hand2")
        self.update_label.pack(side="left", padx=10)

        self.update_label.bind("<Button-1>", lambda event: self.check_for_app_updates(current_version, force_check=True))

        # Make version frame sticky to the bottom
        self.root.grid_rowconfigure(7, weight=1)

        # Set up the grid layout
        self.root.grid_columnconfigure(0, minsize=178)  # Set a fixed minimum width for column 0
        self.root.grid_columnconfigure(1, weight=1)  # Allow column 1 to expand and fill space
        self.root.grid_columnconfigure(2, weight=0, minsize=120)  # Set a fixed minimum width for column 2

        self.update_all_widget_texts()
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

    def open_filter_window(self):
        """Open the filter configuration dialog."""
        self.filter_dialog.open()

    def open_watermark_window(self):
        """Open the watermark configuration dialog."""
        self.watermark_dialog.open()

    def preview_watermark(self, enabled, text, color, size, position, preview_page, origin=None, force_open=True):
        """Preview watermark (delegate to preview window handler)."""
        self.preview_window_handler.preview_watermark(enabled, text, color, size, position, preview_page, origin, force_open)

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
        self.root.after_idle(lambda: self._update_version_info(latest_version, current_version))

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
        self.status_var.set(get_ui_string(self.strings, "status_language_changed"))
        self.update_version_labels_text(
            Version.from_str(self.app_settings.settings["newest_version_available"]), Version.from_str(self.app_settings.settings["version"])
        )
        self.update_version_labels()
        self.root.update_idletasks()
        # Refresh Dev Tools window strings if it's open
        try:
            if hasattr(self, 'dev_tools') and self.dev_tools:
                # dev_tools may be created earlier; ensure it refreshes its UI
                try:
                    self.dev_tools.refresh_ui_strings()
                except Exception:
                    # ignore any errors here - language change should not fail
                    pass
        except Exception:
            pass
        # Refresh filter and watermark dialogs if they're open
        try:
            if hasattr(self, 'filter_dialog') and self.filter_dialog:
                try:
                    self.filter_dialog.refresh_ui_strings()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if hasattr(self, 'watermark_dialog') and self.watermark_dialog:
                try:
                    self.watermark_dialog.refresh_ui_strings()
                except Exception:
                    pass
        except Exception:
            pass

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
            self.status_var.set(get_ui_string(self.strings, "status_imported"))
        else:
            self.status_var.set(get_ui_string(self.strings, "status_waiting"))
            self.root.update_idletasks()

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
            messagebox.showerror(get_ui_string(self.strings, "error"), get_ui_string(self.strings, "val_all_required"))
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
        names = [name.strip() for name in re.split(r",\\s*", self.names_var.get()) if name.strip()]

        try:
            self.paths.is_valid_path(input_file)
            document = Document(input_file)
            if document.is_encrypted:
                messagebox.showerror(get_ui_string(self.strings, "error"), get_ui_string(self.strings, "val_pdf_protected"))
                self.finalize_processing()
                return

            total_matches = 0
            total_skipped = 0
            total_pages = len(document)

            for i in range(len(document)):
                page = document[i]

                if not self.processing_active:  # Check if the process should continue
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
                # Prompt for output file location - schedule on main thread
                self.root.after_idle(lambda: self._handle_save_dialog(document, input_file, total_matches, total_skipped))
            elif self.processing_active and total_marked == 0:
                self.root.after_idle(lambda: messagebox.showinfo(get_ui_string(self.strings, "info"), get_ui_string(self.strings, "val_nothing")))

        except Exception as e:
            error_msg = str(e)
            self.root.after_idle(lambda: messagebox.showerror(get_ui_string(self.strings, "error"), error_msg))
        finally:
            self.root.after_idle(self.finalize_processing)

    def _handle_save_dialog(self, document: Document, input_file: str, total_matches: int, total_skipped: int):
        """Handle the save dialog and file operations on the main thread."""
        self.status_var.set(get_ui_string(self.strings, "status_saving"))
        output_file = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialfile=get_ui_string(self.strings, "file_out_pattern").format(input_file.rsplit(".", 1)[0]),
        )
        if output_file:  # If user specifies a file
            document.save(output_file)
            document.close()
            messagebox.showinfo(
                get_ui_string(self.strings, "status_done"),
                self.get_plural_string("processing_complete", total_matches).format(total_matches, total_skipped),
            )
        else:
            document.close()
            messagebox.showinfo(get_ui_string(self.strings, "info"), get_ui_string(self.strings, "val_no_output"))

    def finalize_processing(self):
        """
        Resets the UI to the initial state, regardless of whether processing was completed or aborted.
        """
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
        self.progress_bar["value"] = (current / total) * 100
        self.status_var.set(self.get_plural_string("processed_pages", matches).format(current, total, matches, skipped))

    def check_for_app_updates(self, current_version: Version = Version.from_str(VERSION_STR), force_check: bool = False):
        """Check for application updates."""
        return self.update_checker.check_for_app_updates(current_version, force_check)

    def start_download(self):
        """Start download process and update UI."""
        self.download_active = True
        self.start_abort_button.config(text=get_ui_string(self.strings, "btn_abort"), command=self.abort_download)
        self.progress_bar["value"] = 0  # Reset progress bar

    def abort_download(self):
        """Abort download process and reset UI."""
        self.download_active = False
        self.start_abort_button.config(text=get_ui_string(self.strings, "btn_start"), command=self.start_processing)
        self.progress_bar["value"] = 0  # Reset progress bar
        self.status_var.set(get_ui_string(self.strings, "upd_cancelled"))

    def finish_download(self):
        """Finish download process and reset UI."""
        self.download_active = False
        # Don't reset button here since app will close after download

    def is_download_aborted(self):
        """Check if download has been aborted."""
        return not self.download_active
