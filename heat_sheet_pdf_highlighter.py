import datetime
import gettext
import json
import os
import pickle
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from tkinter import (
    LEFT,
    SOLID,
    IntVar,
    Label,
    StringVar,
    Tk,
    Toplevel,
    Widget,
    filedialog,
    messagebox,
    ttk,
)
from typing import Dict
import tempfile

import requests
from pymupdf import Page, utils, Rect, Document
from PIL import Image, ImageTk

######################################################################
# Constants
APP_NAME = "Heat Sheet PDF Highlighter"
VERSION_STR = "1.0.0"

CACHE_EXPIRY = datetime.timedelta(days=1)


######################################################################
@dataclass
class Version:
    major: int
    minor: int
    patch: int

    def __str__(self):
        return f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def from_github(cls, version: str):
        """
        Create a Version object from a version string in the format 'vX.Y.Z'.

        Args:
            version (str): The version string.

        Returns:
            Version: The Version object.
        """
        version = version.lstrip("v")
        major, minor, patch = re.findall(r"\d+", version)
        return cls(int(major), int(minor), int(patch))

    @classmethod
    def from_str(cls, version: str):
        """
        Create a Version object from a version string in the format 'X.Y.Z'.

        Args:
            version (str): The version string.

        Returns:
            Version: The Version object.
        """
        major, minor, patch = re.findall(r"\d+", version)
        return cls(int(major), int(minor), int(patch))

    def __lt__(self, other):
        return (self.major, self.minor, self.patch) < (other.major, other.minor, other.patch)

    def __gt__(self, other):
        return (self.major, self.minor, self.patch) > (other.major, other.minor, other.patch)


######################################################################
# Supported languages
global language_options
language_options = ["en", "de"]  # Add more languages as needed


######################################################################
# AppSettings
class AppSettings:
    def __init__(self, settings_file: Path):
        self.settings_file = settings_file
        self.default_settings = {
            "version": VERSION_STR,
            "search_str": "",
            "mark_only_relevant_lines": 1,
            "language": "en",
            "ask_for_update": "True",
            "update_available": "False",
            "newest_version_available": "0.0.0",
        }
        self.settings: Dict = self.load_settings()
        self.validate_settings()

    def load_settings(self) -> Dict:
        """Load settings from a JSON file. If the file doesn't exist, return default settings."""
        if self.settings_file.exists():
            settings: Dict = json.loads(self.settings_file.read_text())
            # validate if from right version and
            if settings.get("version") == VERSION_STR:
                settings = self.validate_settings(settings)
                return settings
            else:
                settings = self.validate_settings(settings)
                return settings
        else:
            return self.default_settings

    def save_settings(self):
        """Save the current settings to a JSON file."""
        self.settings_file.write_text(json.dumps(self.settings, indent=4))

    def update_setting(self, key, value):
        """Update a specific setting and save the file."""
        self.settings[key] = value
        self.save_settings()

    def validate_settings(self, settings: Dict = None):
        """
        Validates the given settings dictionary and updates it with default values if necessary.

        Args:
            settings (Dict, optional): The settings dictionary to be validated. Defaults to None.

        Returns:
            Dict: The validated settings dictionary.
        """
        settings_dict = settings or getattr(self, "settings", {}) or {}
        for key in list(settings_dict.keys()):
            value = settings_dict[key]
            match key:
                case "version":
                    if value != VERSION_STR:
                        # Update the version to the current version
                        settings_dict[key] = VERSION_STR
                case "search_str":
                    if not isinstance(value, str):
                        # Set the default search string if not a string
                        settings_dict[key] = ""
                case "mark_only_relevant_lines":
                    if value not in [0, 1]:
                        # Set the default value for marking only relevant lines if not 0 or 1
                        settings_dict[key] = 1
                case "language":
                    if value not in language_options:
                        # Set the default language if not in the available options
                        settings_dict[key] = "en"
                case "ask_for_update":
                    if value not in [True, False]:
                        # Set the default value for asking for update if not True or False
                        settings_dict[key] = True
                case "update_available":
                    if value not in [True, False]:
                        # Set the default value for update available if not True or False
                        settings_dict[key] = False
                case "newest_version_available":
                    if not isinstance(value, str) or value < VERSION_STR:
                        # Set the default value for newest version available if not a string or smaller than VERSION_STR
                        settings_dict[key] = "0.0.0"
                case _:
                    # Remove any additional keys that are not part of the default settings
                    settings_dict.pop(key)

        # add the default settings if they are not in the settings
        for key, value in self.default_settings.items():
            if key not in settings_dict.keys():
                settings_dict[key] = value
        # if called with settings as argument, update the settings attribute
        if settings:
            return settings_dict
        # if called without argument, update the settings attribute and save the file
        self.settings = settings_dict
        self.save_settings()


def get_settings_path():
    """Determine the correct path for storing application settings based on the operating system."""
    match os.name:
        case "nt":  # Windows
            appdata = Path(os.getenv("APPDATA"))  # Roaming folder
            settings_dir = appdata / APP_NAME
        case "posix":
            if "darwin" in os.sys.platform:  # macOS
                settings_dir = Path.home() / f"Library/Application Support/{APP_NAME}"
            else:  # Assuming Linux
                settings_dir = Path.home() / f".config/{APP_NAME}"
        case _:
            raise Exception("Unsupported OS")

    settings_dir.mkdir(parents=True, exist_ok=True)
    return settings_dir


#######################################################################
# Paths

# get the bundle dir if bundled or simply the __file__ dir if not bundled
bundle_dir = getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)  # get the bundle dir if bundled or simply the __file__ dir if not bundled
locales_dir = Path(bundle_dir) / "locales"  # get the locales dir

# Add the path to the Breeze theme
tcl_lib_path = Path(__file__).resolve().parent / "assets" / "ttk-Breeze-0.6"

# Add the path to the icon
icon_path = Path(__file__).resolve().parent / "assets" / "icon_no_background.ico"

# Add the path to the logo
logo_path = Path(__file__).resolve().parent / "assets" / "logo_no_background.png"

# File path for the settings
settings_path = get_settings_path()
SETTINGS_FILE = settings_path / "settings.json"

CACHE_FILE = settings_path / "update_check_cache.pkl"

# Add the path to the update script
UPDATE_SCRIPT_PATH = Path(__file__).resolve().parent / "update_app.bat"

#####################################################################################
# PDF Processing functions


def get_line_bbox(page: Page, match_rect: Rect):
    """
    Get the bounding box of the line containing the given match rectangle.

    Args:
        page (Page): The PDF page object.
        match_rect (Rect): The rectangle representing the match.

    Returns:
        Rect: The bounding box of the line containing the match rectangle.
    """
    words = utils.get_text(page, "words")
    line_rect = Rect(match_rect)
    match_height = match_rect.y1 - match_rect.y0
    threshold = match_height * 0.5

    for word in words:
        word_rect = Rect(word[:4])
        if abs(word_rect.y0 - match_rect.y0) <= threshold and abs(word_rect.y1 - match_rect.y1) <= threshold:
            line_rect = line_rect | word_rect

    return line_rect


def highlight_matching_data(page: Page, search_str: str, only_relevant: bool = False):
    """
    Highlights the matching data on a given page based on the search string.

    Args:
        page (Page): The page object on which to highlight the data.
        search_str (str): The string to search for and highlight.
        only_relevant (bool, optional): If True, only highlights the data if it matches the relevant line pattern.
            Defaults to False.

    Returns:
        Tuple[int, int]: A tuple containing the number of matches found and the number of matches skipped.

    """
    matches_found = 0
    skipped_matches = 0
    text_instances = utils.search_for(page, search_str)

    # Adjusted regex to consider new lines between elements of the pattern
    relevant_line_pattern = re.compile(
        r"(?i)(?:Bahn\s)?\d+\s.*?\s" + re.escape(search_str) + r"\s.*?(?:(?:\d{2}[:.,]\d{2}(?:,|\.)\d{2})|(?:\d{2},\d{2})|(?:\d{2}\.\d{2})|NT)",
        re.DOTALL,  # Allows for matching across multiple lines
    )

    for inst in text_instances:
        # Increment matches found
        matches_found += 1

        if only_relevant:
            # Find the line of text that contains the instance
            line_rect = get_line_bbox(page, inst)  # Get the bounding box for the entire line
            line_text = utils.get_text(page, "text", clip=line_rect)  # Extract text within this rectangle
            # Check if the extracted line matches the relevant line pattern
            if not re.search(relevant_line_pattern, line_text):
                skipped_matches += 1
                continue  # Skip highlighting if the line does not match the pattern

        # Highlight the line if it matches the pattern or if only_relevant is False
        line_rect = get_line_bbox(page, inst)  # Ensure this line is included for both conditions
        highlight = page.add_highlight_annot(line_rect)
        highlight.update()

    return matches_found, skipped_matches


#####################################################################################
# Path validation


def is_valid_path(path: str):
    """
    Checks if the given path is a valid file path.

    Args:
        path (str): The path to be checked.

    Returns:
        str: The valid file path.

    Raises:
        ValueError: If the path is empty.
        FileNotFoundError: If the file is not found at the given path.
        ValueError: If the path is not a valid file path.
    """

    if not path:
        raise ValueError("Invalid Path")
    if not Path(path).exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not Path(path).is_file():
        raise ValueError(f"Invalid file path: {path}")
    return path


#####################################################################################
# GUI


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
        self.root.tk.call("lappend", "auto_path", tcl_lib_path)
        # self.root.tk.call("package", "require", "ttk::theme::Breeze")
        # Set up internationalization
        self.lang = gettext.translation("base", localedir=locales_dir, languages=language_options)
        self.lang.install()
        self._ = staticmethod(self.lang.gettext)
        self.n_ = staticmethod(self.lang.ngettext)

        self.init_translatable_strings_version()

        # Initialize the settings
        self.app_settings = AppSettings(SETTINGS_FILE)

        # Check for updates
        threading.Thread(target=self.check_for_app_updates, daemon=True).start()

        # Initialize the UI components
        self.setup_ui()

    def init_translatable_strings_version(self):
        self.translatable_strings_version = {
            "version_update_failed": self._("Version: {0} (Update check failed)"),
            "version_new_available": self._("Version: {0} (New version available)"),
            "version_no_update": self._("Version: {0}"),
        }
        self.translatable_strings_update = {
            "Check for Updates": self._("Check for Updates"),
            "Install Update": self._("Install Update"),
        }

    def setup_ui(self):
        """
        Sets up the user interface for the PDFHighlighterApp.
        """
        title_text = self._("Heat sheet highlighter")
        self.root.title(title_text)
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
        self.root.iconbitmap(icon_path)

        # Load and display the logo
        logo_image = Image.open(logo_path)
        title_font = ("Arial", 16, "bold")
        title_height = 50  # Set the desired height of the title
        logo_image = logo_image.resize((title_height, title_height))
        logo_photo = ImageTk.PhotoImage(logo_image)
        logo_label = Label(self.root, image=logo_photo)
        logo_label.image = logo_photo  # Store a reference to the image to prevent it from being garbage collected
        logo_label.grid(row=0, column=0, sticky="W", padx=10, pady=10)

        # Application title next to the logo
        self.title = ttk.Label(self.root, text=title_text, font=title_font)
        self.title.grid(row=0, column=1, sticky="EW", padx=10, pady=10)

        lang_var = StringVar()
        lang_var.set(self.app_settings.settings["language"])
        lang_var.trace_add("write", lambda *args: self.app_settings.update_setting("language", lang_var.get()))

        self.language_menu = ttk.OptionMenu(
            self.root, lang_var, self.app_settings.settings["language"], *language_options, command=self.on_language_change
        )
        self.language_menu.grid(row=0, column=2, sticky="E", padx=10, pady=10)
        Tooltip(self.language_menu, text=self._("Select the language"))

        # PDF file selection
        self.label_pdf_file = ttk.Label(self.root, text=self._("PDF-File:"))
        self.label_pdf_file.grid(row=1, column=0, sticky="E", padx=10, pady=2)
        self.pdf_file_var = StringVar()
        self.entry_file = ttk.Entry(self.root, textvariable=self.pdf_file_var, state="readonly")
        self.entry_file.grid(row=1, column=1, sticky="WE", padx=10)
        Tooltip(self.entry_file, text=self._("Select the heat sheet pdf."))
        Tooltip(self.label_pdf_file, text=self._("Select the heat sheet pdf."))

        self.browse_button = ttk.Button(self.root, text=self._("Browse"), command=self.browse_file, width=11)
        self.browse_button.grid(row=1, column=2, padx=10, sticky="E")

        # Search term entry
        self.label_search_str = ttk.Label(self.root, text=self._("Search term (Club name):"))
        self.label_search_str.grid(row=2, column=0, sticky="E", padx=10, pady=2)

        self.search_phrase_var = StringVar()
        self.search_phrase_var.set(self.app_settings.settings["search_str"])

        self.entry_search_str = ttk.Entry(self.root, textvariable=self.search_phrase_var)
        self.entry_search_str.grid(row=2, column=1, sticky="WE", columnspan=2, padx=10)
        Tooltip(self.label_search_str, text=self._("Enter the name of the club to highlight the results."))
        Tooltip(self.entry_search_str, text=self._("Enter the name of the club to highlight the results."))

        # Options for highlighting
        self.relevant_lines_var = IntVar()
        self.relevant_lines_var.set(self.app_settings.settings["mark_only_relevant_lines"])
        self.relevant_lines_var.trace_add(
            "write", lambda *args: self.app_settings.update_setting("mark_only_relevant_lines", self.relevant_lines_var.get())
        )

        self.checkbox_relevant_lines = ttk.Checkbutton(self.root, text=self._("Try to mark only relevant lines"), variable=self.relevant_lines_var)
        self.checkbox_relevant_lines.grid(row=3, column=0, columnspan=3, sticky="W", padx=10, pady=2)
        Tooltip(self.checkbox_relevant_lines, text=self._("Only highlights the lines that contain the search term and match the expected format."))

        # Progress bar
        self.progress = ttk.Progressbar(self.root, orient="horizontal", length=400, mode="determinate")
        self.progress.grid(row=4, column=0, columnspan=3, padx=10, pady=20, sticky="WE")

        # Status label
        self.status_var = StringVar(value=self._("Status: Waiting"))
        ttk.Label(self.root, textvariable=self.status_var).grid(row=5, column=0, columnspan=3, padx=10, pady=2)

        # Start/Abort button
        self.start_abort_button = ttk.Button(self.root, text=self._("Start"), command=self.start_processing)
        self.start_abort_button.grid(row=6, column=1, pady=10)
        Tooltip(self.start_abort_button, text=self._("Start or cancel the highlight process."))

        # add version label and update button
        self.version_frame = ttk.Frame(self.root)
        self.version_frame.grid(row=7, column=0, columnspan=3, padx=10, pady=2, sticky="WE")

        current_version = Version.from_str(self.app_settings.settings["version"])
        latest_version = Version.from_str(self.app_settings.settings["newest_version_available"])
        self.update_version_labels_text(latest_version, current_version)

        self.version_label = ttk.Label(self.version_frame, text=self.version_label_text, foreground=self.version_color)
        self.version_label.pack(side="left")

        self.update_label = ttk.Label(self.version_frame, text=self.update_label_text, foreground="#5c5c5c", cursor="hand2")
        self.update_label.pack(side="left", padx=10)

        self.update_label.bind("<Button-1>", lambda event: self.check_for_app_updates(current_version, force_check=True))
        # self.update_label.bind("<Button-1>", lambda event: self.test_install_routine())

        # make version frame sticky to the bottom
        self.root.grid_rowconfigure(7, weight=1)

        # Set up the grid layout
        self.root.grid_columnconfigure(0, minsize=178)  # Set a fixed minimum width for column 0
        self.root.grid_columnconfigure(1, weight=1)  # Allow column 1 to expand and fill space
        self.root.grid_columnconfigure(2, weight=0, minsize=120)  # Set a fixed minimum width for column 2

        # Set the initial state of the UI based on the settings
        self.on_language_change(self.app_settings.settings["language"])

    def update_version_labels_text(self, latest_version: Version, current_version: Version = Version.from_str(VERSION_STR)):
        self.init_translatable_strings_version()
        if latest_version is None or latest_version is False or latest_version < current_version:
            self.version_label_text = self.translatable_strings_version["version_update_failed"]
            self.version_color = "#9d6363"
            self.update_label_text = self.translatable_strings_update["Check for Updates"]
        elif latest_version and latest_version > current_version:
            self.version_label_text = self.translatable_strings_version["version_new_available"]
            self.version_color = "#ff9f14"
            self.update_label_text = self.translatable_strings_update["Install Update"]
        else:
            self.version_label_text = self.translatable_strings_version["version_no_update"]
            self.version_color = "#808080"
            self.update_label_text = self.translatable_strings_update["Check for Updates"]

    def update_version_labels(self):
        self.version_label.config(text=self.version_label_text.format(self.app_settings.settings["version"]), foreground=self.version_color)
        self.update_label.config(text=self.update_label_text)
        self.root.update_idletasks()

    def on_language_change(self, language: str):
        """
        Change the language of the application.

        Args:
            language (str): The language code to switch to.
        """
        self.lang = gettext.translation("base", localedir=locales_dir, languages=[language])
        self.lang.install()
        self._ = staticmethod(self.lang.gettext)
        self.n_ = staticmethod(self.lang.ngettext)

        # Update the text in the GUI
        self.label_pdf_file.config(text=self._("PDF-File:"))
        self.label_search_str.config(text=self._("Search term (Club name):"))
        self.checkbox_relevant_lines.config(text=self._("Try to mark only relevant lines"))
        self.status_var.set(self._("Status: Language changed to english"))
        self.start_abort_button.config(text=self._("Start"))
        self.browse_button.config(text=self._("Browse"))
        self.title.config(text=self._("Heat sheet highlighter"))
        self.language_menu.config(text=self._("Select language"))
        self.root.title(self._("Heat sheet highlighter"))
        self.init_translatable_strings_version()
        self.update_version_labels_text(
            Version.from_str(self.app_settings.settings["newest_version_available"]), Version.from_str(self.app_settings.settings["version"])
        )
        self.update_version_labels()
        self.root.update_idletasks()

        # Update the tooltips
        Tooltip(self.entry_file, text=self._("Select the heat sheet pdf."))
        Tooltip(self.label_pdf_file, text=self._("Select the heat sheet pdf."))
        Tooltip(self.label_search_str, text=self._("Enter the name of the club to highlight the results."))
        Tooltip(self.entry_search_str, text=self._("Enter the name of the club to highlight the results."))
        Tooltip(self.checkbox_relevant_lines, text=self._("Only highlights the lines that contain the search term and match the expected format."))
        Tooltip(self.start_abort_button, text=self._("Start or cancel the highlight process."))
        Tooltip(self.language_menu, text=self._("Select the language"))

    def browse_file(self):
        """
        Opens a file dialog to browse and select a PDF file.
        """
        self.status_var.set(self._("Status: Importing PDF. Please wait..."))
        self.root.update_idletasks()
        file_path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if file_path:
            file_name = Path(file_path).name
            self.pdf_file_var.set(file_name)  # Display only the file name
            self.input_file_full_path = file_path  # Store full path for processing
            self.status_var.set(self._("Status: PDF imported."))
        else:
            self.status_var.set(self._("Status: Waiting"))
            self.root.update_idletasks()

    def start_processing(self):
        """
        Starts the PDF processing based on the selected file and search parameters.
        """
        # Change the button to "Abort" and its command to abort_processing
        self.start_abort_button.config(text=self._("Abort"), command=self.finalize_processing)

        # Set processing flag to True
        self.processing_active = True

        # update the search string in the settings
        if self.search_phrase_var.get() != self.app_settings.settings["search_str"]:
            self.app_settings.update_setting("search_str", self.search_phrase_var.get())

        # Start the processing in a separate thread
        input_file = getattr(self, "input_file_full_path", None)
        search_str = self.search_phrase_var.get()
        only_relevant = bool(self.relevant_lines_var.get())

        if not all([input_file, search_str]):
            messagebox.showerror(self._("Error"), self._("All fields are required!"))
            self.finalize_processing()
            return

        threading.Thread(target=self.process_pdf, args=(input_file, search_str, only_relevant), daemon=True).start()

    def process_pdf(self, input_file: str, search_str: str, only_relevant: bool):
        self.processing_active = True
        try:
            is_valid_path(input_file)
            document = Document(input_file)
            if document.is_encrypted:
                messagebox.showerror(self._("Error"), self._("Password-protected PDFs are not supported."))
                self.finalize_processing()
                return
            output_buffer = BytesIO()
            total_matches = 0
            total_skipped = 0
            total_pages = len(document)

            for i, page in enumerate(document, start=1):
                if not self.processing_active:  # Check if the process should continue
                    self.status_var.set(self._("Status: Aborted by user."))
                    self.finalize_processing()
                    return

                matches_found, skipped_matches = highlight_matching_data(page, search_str, only_relevant)
                total_matches += matches_found
                total_skipped += skipped_matches
                self.update_progress(i, total_pages, total_matches, total_skipped)

            total_marked = total_matches - total_skipped

            if self.processing_active and total_marked > 0:  # Check we finished normally and have matches
                # Prompt for output file location
                self.root.update_idletasks()  # Ensure GUI is updated before showing dialog
                self.status_var.set(self._("Status: Saving PDF.. Please wait..."))
                self.root.update_idletasks()  # Ensure GUI is updated
                output_file = filedialog.asksaveasfilename(
                    defaultextension=".pdf",
                    filetypes=[("PDF files", "*.pdf")],
                    initialfile=self._("{0}_marked.pdf").format(input_file.rsplit(".", 1)[0]),
                )
                if output_file:  # If user specifies a file
                    document.save(output_buffer)
                    with open(output_file, mode="wb") as f:
                        f.write(output_buffer.getbuffer())
                    messagebox.showinfo(
                        self._("Finished"),
                        self.n_(
                            "Processing complete: {0} match found. {1} skipped.",
                            "Processing complete: {0} matches found. {1} skipped.",
                            total_matches,
                        ).format(total_matches, total_skipped),
                    )
                else:
                    messagebox.showinfo(self._("Info"), self._("No output file selected; processing aborted after matches were found."))
            elif self.processing_active and total_marked == 0:
                messagebox.showinfo(self._("Info"), self._("Nothing to highlight; no file saved."))

            document.close()
        except Exception as e:
            messagebox.showerror("Error", str(e))
        finally:
            self.finalize_processing()

    def finalize_processing(self):
        """
        Resets the UI to the initial state, regardless of whether processing was completed or aborted.
        """
        # This method should reset the UI components (progress bar, status message, start/abort button) ensuring it reflects the current state accurately.
        self.progress["value"] = 0  # Reset progress bar
        # Reset the button to "Start" with the original command
        self.start_abort_button.config(text=self._("Start"), command=self.start_processing)
        if not self.processing_active:  # Only update the status if the processing was aborted
            self.status_var.set(self._("Status: Processing aborted."))
        else:
            self.status_var.set(self._("Status: Waiting"))
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
        self.progress["value"] = (current / total) * 100
        self.status_var.set(
            self.n_(
                "Processed: {0}/{1} pages. {2} match found. {3} skipped.", "Processed: {0}/{1} pages. {2} matches found. {3} skipped.", matches
            ).format(current, total, matches, skipped)
        )
        self.root.update_idletasks()

    def check_for_app_updates(self, current_version: Version = Version.from_str(VERSION_STR), force_check: bool = False):
        """
        Check for updates and prompt the user to install if a new version is available.
        """
        now = datetime.datetime.now()

        if not force_check and os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "rb") as f:
                cache_time, latest_version = pickle.load(f)
                if now - cache_time < CACHE_EXPIRY:
                    return latest_version

        # Perform the update check...
        latest_version = self._get_latest_version_from_github(current_version=current_version, force_check=force_check)

        # Cache the result
        with open(CACHE_FILE, "wb") as f:
            pickle.dump((now, latest_version), f)

        # update the version label
        self.update_version_labels_text(latest_version, current_version)
        self.update_version_labels()

        return latest_version

    def _get_latest_version_from_github(self, current_version: Version = Version.from_str(VERSION_STR), force_check: bool = False):
        # GitHub release URL
        release_url = "https://api.github.com/repos/jonalbr/heat-sheet-pdf-highlighter/releases/latest"

        try:
            # Send GET request to GitHub API
            response = requests.get(release_url)
            response.raise_for_status()

            # Parse the response JSON
            release_info = response.json()

            # Get the latest version number and download URL
            latest_version = Version.from_github(release_info["tag_name"])
            download_url = release_info["assets"][0]["browser_download_url"]

            # reset ask_for_update if newer version than in newest_version_available is found
            if latest_version > Version.from_str(self.app_settings.settings["newest_version_available"]):
                self.app_settings.update_setting("ask_for_update", True)
                # safe the newest version in the settings
                self.app_settings.update_setting("newest_version_available", str(latest_version))

            # Compare the latest version with the current version
            if latest_version > current_version and (self.app_settings.settings["ask_for_update"] or force_check):
                # update

                # Prompt the user to install the update
                update_choice = messagebox.askyesnocancel(
                    self._("Update Available"),
                    self._(f"A new version ({0}) is available. Do you want to update?").format(latest_version),
                    icon="question",
                    default="yes",
                    parent=self.root,
                )
                if update_choice is None:
                    # User clicked "Aboard" - will ask again next time
                    pass
                elif update_choice:
                    # User clicked "Yes"
                    self.download_and_run_installer(download_url)
                else:
                    # Inform the user that they will not be asked again, but if there is a new version, they can still check manually
                    # also if there is a newer new version than the one in newest_version_available, they will be asked again
                    choice = messagebox.askokcancel(
                        self._("Update Information"),
                        self._(
                            "Click 'yes' to not be asked again for this update. You can still check manually for updates. If there is a newer version available, you will be asked again."
                        ),
                    )
                    if choice:
                        self.app_settings.update_setting("ask_for_update", False)
            else:
                if force_check:
                    # Inform the user that they are already up to date
                    messagebox.showinfo(self._("Up to Date"), self._("You are already using the latest version."))
            return latest_version
        except requests.exceptions.RequestException as e:
            if force_check:
                # Handle any errors that occur during the update check
                choice = messagebox.askretrycancel(self._("Update Error"), self._("Failed to check for updates: {0}").format(str(e)))
                if choice:
                    self.check_for_app_updates(current_version, force_check)
            else:
                print(f"Failed to check for updates: {str(e)}")
            return False

    def download_and_run_installer(self, download_url: str):
        """
        Downloads the installer from the given URL and runs it.

        Args:
            download_url (str): The URL to download the installer from.
        """
        # Create a temporary file for the installer
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as temp_file:
            installer_path = Path(temp_file.name)

        # Download the installer exe
        try:
            response = requests.get(download_url)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            messagebox.showerror(self._("Error"), self._("Failed to download the installer: {0}").format(str(e)))

        # Write the installer to the file
        installer_path.write_bytes(response.content)

        # Close the application
        self.root.destroy()

        # Get the current process id
        pid = os.getpid()

        # Create a STARTUPINFO object
        startupinfo = subprocess.STARTUPINFO()

        # Set the STARTF_USESHOWWINDOW flag
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        # Run the update script without showing a window
        subprocess.Popen([UPDATE_SCRIPT_PATH, str(pid), installer_path], startupinfo=startupinfo)

    def test_install_routine(self, installer_path: Path = None):
        if not installer_path:
            installer_path = filedialog.askopenfilename(filetypes=[("Installer files", "*.exe")])
        if not installer_path:
            messagebox.showerror(self._("Error"), self._("No installer selected."))
            return

        # Create a temporary file for the installer
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as temp_file:
            installer_path_temp = Path(temp_file.name)
            print(installer_path_temp)

        # Write the installer to the file
        installer_path = Path(installer_path)
        buffer = installer_path.read_bytes()
        installer_path_temp.write_bytes(buffer)

        # Close the application
        self.root.destroy()

        # Get the current process id
        pid = os.getpid()

        # Create a STARTUPINFO object
        startupinfo = subprocess.STARTUPINFO()

        # Set the STARTF_USESHOWWINDOW flag
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        # Run the update script without showing a window
        subprocess.Popen([UPDATE_SCRIPT_PATH, str(pid), installer_path_temp], startupinfo=startupinfo)


#####################################################################################
# Tooltip class


class Tooltip:
    """
    Create a tooltip for a given widget.
    """

    def __init__(self, widget: Widget, text: str):
        self.widget = widget
        self.text = text

        # Set up internationalization
        self.lang = gettext.translation("base", localedir=locales_dir, languages=language_options)
        self.lang.install()
        self._ = staticmethod(self.lang.gettext)  # alias for convenience

        self.tipwindow = None
        self.id = None
        self.x = self.y = 0
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        "Display text in tooltip window"
        self.x = self.widget.winfo_rootx() + 20
        self.y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tipwindow = tw = Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry("+%d+%d" % (self.x, self.y))
        label = Label(tw, text=self.text, justify=LEFT, background="#ffffe0", relief=SOLID, borderwidth=1, font=("tahoma", "8", "normal"))
        label.pack(ipadx=1)

    def hide_tip(self, event=None):
        tw = self.tipwindow
        self.tipwindow = None
        if tw:
            tw.destroy()


if __name__ == "__main__":
    root = Tk()
    app = PDFHighlighterApp(root)
    root.mainloop()
