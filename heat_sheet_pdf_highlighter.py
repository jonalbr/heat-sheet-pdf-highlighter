import csv
import datetime
import gettext
import json
import os
import pickle
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from io import BytesIO
from pathlib import Path
from tkinter import (
    LEFT,
    SOLID,
    WORD,
    IntVar,
    Label,
    StringVar,
    Text,
    Tk,
    Toplevel,
    Widget,
    filedialog,
    messagebox,
    ttk,
)
from tkinter import Button as tkButton
from typing import Dict, List

import requests
from PIL import Image, ImageTk
from pymupdf import Document, Page, Rect, utils, Pixmap

######################################################################
# Constants
APP_NAME = "Heat Sheet PDF Highlighter"
VERSION_STR = "1.2.0"

CACHE_EXPIRY = datetime.timedelta(days=1)


######################################################################
@dataclass
class Version:
    major: int = 0
    minor: int = 0
    patch: int = 0
    rc: int = None

    def __str__(self):
        if self.rc:
            return f"{self.major}.{self.minor}.{self.patch}-rc{self.rc}"
        else:
            return f"{self.major}.{self.minor}.{self.patch}"

    @classmethod
    def from_str(cls, version: str):
        """
        Create a Version object from a version string in the format 'X.Y.Z' or 'X.Y.Z-rcN'.

        Args:
            version (str): The version string.

        Returns:
            Version: The Version object.
        """
        # Extract the version numbers and the optional rc number
        parts = re.findall(r"\d+", version)

        # Convert the version numbers to integers
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2])

        # If there's a fourth part, it's the rc number
        rc = int(parts[3]) if len(parts) > 3 else None

        return cls(major, minor, patch, rc)

    def __lt__(self, other):
        self_rc = self.rc if self.rc is not None else -1
        other_rc = other.rc if other.rc is not None else -1
        return (self.major, self.minor, self.patch, self_rc) < (other.major, other.minor, other.patch, other_rc)

    def __gt__(self, other):
        self_rc = self.rc if self.rc is not None else -1
        other_rc = other.rc if other.rc is not None else -1
        return (self.major, self.minor, self.patch, self_rc) > (other.major, other.minor, other.patch, other_rc)


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
            "mark_only_relevant_lines": 1,  # 0 or 1
            "enable_filter": 0,  # 0 or 1
            "highlight_mode": "NAMES_DIFF_COLOR",  # "ONLY_NAMES" or "NAMES_DIFF_COLOR
            "language": "en",
            "ask_for_update": "True",
            "update_available": "False",
            "newest_version_available": "0.0.0",
            "beta": "False",
            "names": "",
            "watermark_enabled": "False",
            "watermark_text": "",
            "watermark_color": "#FFA500",
            "watermark_size": 16,
            "watermark_position": "top",
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
                case "enable_filter":
                    if value not in [0, 1]:
                        # Set the default value for enabling the filter if not 0 or 1
                        settings_dict[key] = 0
                case "highlight_mode":
                    if value not in ["ONLY_NAMES", "NAMES_DIFF_COLOR"]:
                        # Set the default value for highlighting mode if not "ONLY_NAMES" or "NAMES_DIFF_COLOR"
                        settings_dict[key] = "NAMES_DIFF_COLOR"
                case "language":
                    if value not in language_options:
                        # Set the default language if not in the available options
                        settings_dict[key] = "en"
                case "ask_for_update":
                    if value not in ["True", "False"]:
                        # Set the default value for asking for update if not True or False
                        settings_dict[key] = "True"
                case "update_available":
                    if value not in ["True", "False"]:
                        # Set the default value for update available if not True or False
                        settings_dict[key] = "False"
                case "newest_version_available":
                    if not isinstance(value, str) or value < VERSION_STR:
                        # Set the default value for newest version available if not a string or smaller than VERSION_STR
                        settings_dict[key] = "0.0.0"
                case "names":
                    if not isinstance(value, str):
                        # Set the default value for names if not a string
                        settings_dict[key] = ""
                case "beta":
                    if value not in ["True", "False"]:
                        # Set the default value for beta if not True or False
                        settings_dict[key] = "False"
                # New watermark validations:
                case "watermark_enabled":
                    if value not in ["True", "False"]:
                        settings_dict[key] = "False"
                case "watermark_text":
                    if not isinstance(value, str):
                        settings_dict[key] = ""
                case "watermark_color":
                    if not (isinstance(value, str) and value.startswith("#")):
                        settings_dict[key] = "#FFA500"
                case "watermark_size":
                    try:
                        if int(value) <= 0:
                            settings_dict[key] = 16
                    except:  # noqa: E722
                        settings_dict[key] = 16
                case "watermark_position":
                    if value not in ["top", "bottom"]:
                        settings_dict[key] = "top"
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
bundle_dir = getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)
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


class HighlightMode(IntEnum):
    ONLY_NAMES = 0
    NAMES_DIFF_COLOR = 1


def highlight_matching_data(
    page: Page,
    search_str: str,
    only_relevant: bool = True,
    filter_enabled: bool = False,
    names: List[str] = [],
    highlight_mode: HighlightMode = HighlightMode.NAMES_DIFF_COLOR,
):
    """
    Highlights the matching data on a given page based on the search string.

    Args:
        page (Page): The page object on which to highlight the data.
        search_str (str): The string to search for and highlight.
        only_relevant (bool, optional): If True, only highlights the data if it matches the relevant line pattern.
            Defaults to False.
        filter_enabled (bool, optional): If True, enables filtering based on the relevant line pattern.
            Defaults to False.
        names (List[str], optional): A list of names to filter the data. Only lines containing any of these names will be highlighted.
            Defaults to an empty list.
        highlight_mode (HighlightMode, optional): The highlight mode to use. Can be one of the values from the HighlightMode enum.
            Defaults to HighlightMode.NAMES_DIFF_COLOR.

    Returns:
        Tuple[int, int]: A tuple containing the number of matches found and the number of matches skipped.

    """
    matches_found = 0
    skipped_matches = 0
    text_instances = utils.search_for(page, search_str)

    # Adjusted regex to consider new lines between elements of the pattern
    relevant_line_pattern = re.compile(
        r"(?i)(?:Bahn\s)?\d+\s.*?\s" + re.escape(search_str) + r"\s.*?(?:\d{1,2}[:.,;]\d{2}(?:,|\.)\d{2}|\d{1,2}[:.,;]\d{2}|NT|ohne)",
        re.DOTALL,  # Allows for matching across multiple lines
    )
    names_pattern = re.compile(r"\b(?:{})\b".format("|".join([re.escape(name) for name in names])), re.IGNORECASE)

    for inst in text_instances:
        # Increment matches found
        matches_found += 1
        line_rect = get_line_bbox(page, inst)  # Get the bounding box for the entire line
        if only_relevant:
            # Find the line of text that contains the instance
            line_text = utils.get_text(page, "text", clip=line_rect)  # Extract text within this rectangle

            # Check if the extracted line matches the relevant line pattern
            if not re.search(relevant_line_pattern, line_text):
                skipped_matches += 1
                continue  # Skip highlighting if the line does not match the pattern

            if highlight_mode == HighlightMode.ONLY_NAMES and not names_pattern.search(line_text) and filter_enabled:
                skipped_matches += 1
                continue  # Skip highlighting if the line does not contain any of the names

            highlight = page.add_highlight_annot(line_rect)

            if highlight_mode == HighlightMode.NAMES_DIFF_COLOR and names_pattern.search(line_text) and filter_enabled:
                # light highlight blue
                highlight.set_colors(stroke=[196 / 255, 250 / 255, 248 / 255])
                highlight.update()
            else:
                highlight.set_colors(stroke=[255 / 255, 255 / 255, 166 / 255])
                highlight.update()
        else:
            # Highlight the line if only_relevant is False
            highlight = page.add_highlight_annot(line_rect)
            highlight.update()

    return matches_found, skipped_matches


def add_watermark(page: Page, text: str, font_size: int, color_hex: str, position: str):
    """
    Adds a watermark on the given PDF page.
    """
    # Convert hex color to normalized RGB tuple
    r = int(color_hex[1:3], 16) / 255
    g = int(color_hex[3:5], 16) / 255
    b = int(color_hex[5:7], 16) / 255
    rect = page.rect
    try:
        from PIL import ImageFont

        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        from PIL import ImageFont

        font = ImageFont.load_default()
    bbox = font.getbbox(text)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    if position == "top":
        text_x = rect.x0 + (rect.width - text_width) / 2
        text_y = rect.y0 + 20
    elif position == "bottom":
        text_x = rect.x0 + (rect.width - text_width) / 2
        text_y = rect.y1 - text_height - 20
    page: utils = page
    page.insert_text((text_x, text_y), text, fontsize=font_size, color=(r, g, b))


def watermark_pdf_page(page: Page, settings: Dict):
    """
    Applies watermark on a PDF page using settings.
    """
    if settings.get("watermark_enabled") == "True" and settings.get("watermark_text"):
        add_watermark(
            page,
            text=settings.get("watermark_text"),
            font_size=int(settings.get("watermark_size")),
            color_hex=settings.get("watermark_color"),
            position=settings.get("watermark_position"),
        )


# Replace overlay_watermark_on_image with:
def overlay_watermark_on_image(image, text: str, font_size: int, color_hex: str, position: str):
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()
    # Use textbbox to get dimensions
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    img_width, img_height = image.size
    if position == "top":
        pos = ((img_width - text_width) / 2, 10)
    else:  # bottom
        pos = ((img_width - text_width) / 2, img_height - text_height - 10)
    # Solid color (opacity set to full)
    draw.text(pos, text, font=font, fill=color_hex)
    return image


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
        self.preview_window = None  # Track active preview window
        self.current_preview_page = 1
        self.last_watermark_data = {}  # To store last settings
        self.last_preview_origin = None

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

        # frame  for filters
        self.filter_frame = ttk.Frame(self.root)
        self.filter_frame.grid(row=3, column=0, columnspan=3, sticky="WE", pady=2)

        self.filter_frame.grid_columnconfigure(1, weight=1)

        # Options for highlighting
        self.relevant_lines_var = IntVar()
        self.relevant_lines_var.set(self.app_settings.settings.get("mark_only_relevant_lines", 1))
        self.relevant_lines_var.trace_add(
            "write", lambda *args: self.app_settings.update_setting("mark_only_relevant_lines", self.relevant_lines_var.get())
        )

        self.checkbox_relevant_lines = ttk.Checkbutton(self.filter_frame, text=self._("Mark only relevant lines"), variable=self.relevant_lines_var)
        self.checkbox_relevant_lines.grid(row=0, column=0, sticky="W", padx=10)
        Tooltip(self.checkbox_relevant_lines, text=self._("Only highlights the lines that contain the search term and match the expected format."))

        # Filter
        self.names_var = StringVar()
        self.names_var.set(self.app_settings.settings.get("names", ""))
        self.names_var.trace_add("write", lambda *args: self.app_settings.update_setting("names", self.names_var.get()))

        self.highlight_mode_var = StringVar()
        self.highlight_mode_var.set(self.app_settings.settings.get("highlight_mode", HighlightMode.NAMES_DIFF_COLOR.name))
        self.highlight_mode_var.trace_add("write", lambda *args: self.app_settings.update_setting("highlight_mode", self.highlight_mode_var.get()))

        self.enable_filter_var = IntVar()
        self.enable_filter_var.set(self.app_settings.settings.get("enable_filter", 0))
        self.enable_filter_var.trace_add("write", lambda *args: self.app_settings.update_setting("enable_filter", self.enable_filter_var.get()))

        self.button_filter = ttk.Button(self.filter_frame, text=self._("Filter"), command=self.open_filter_window)
        self.button_filter.grid(row=0, column=1, sticky="E", padx=10)
        Tooltip(self.button_filter, text=self._("Configure highlighting lines with specific names."))

        self.button_watermark = ttk.Button(self.filter_frame, text=self._("Watermark"), command=self.open_watermark_window)
        self.button_watermark.grid(row=0, column=2, sticky="E", padx=10)
        Tooltip(self.button_watermark, text=self._("Configure watermark options."))

        # Progress bar
        self.progress_bar = ttk.Progressbar(self.root, orient="horizontal", length=400, mode="determinate")
        self.progress_bar.grid(row=4, column=0, columnspan=3, padx=10, pady=20, sticky="WE")

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

    def open_filter_window(self):
        window = Toplevel(self.root)
        window.title(self._("Filter"))
        window.grab_set()
        window.focus_set()

        def apply_changes():
            self.names_var.set(entry_names.get("1.0", "end-1c"))
            self.highlight_mode_var.set(temp_highlight_mode_var.get())
            self.enable_filter_var.set(self.enable_filter_var.get())
            window.destroy()

        def clear_text(*args):
            entry_names.delete("1.0", "end")

        def import_names(*args):
            filename = filedialog.askopenfilename(
                parent=window, filetypes=[(self._("CSV and Text files"), "*.csv;*.txt"), (self._("All files"), "*.*")]
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

        self.checkbox_filter = ttk.Checkbutton(window, text=self._("Enable Filter"), variable=self.enable_filter_var)
        self.checkbox_filter.grid(row=0, column=0, columnspan=2, sticky="W", padx=10, pady=5)
        Tooltip(self.checkbox_filter, text=self._("Enable highlighting lines with specific names."))

        temp_highlight_mode_var = StringVar(value=self.highlight_mode_var.get())
        label_names = ttk.Label(window, text=self._("Names"))
        label_names.grid(row=1, column=0, sticky="W", padx=10)

        entry_names = Text(window, height=6, width=50, wrap=WORD)
        entry_names.insert(1.0, self.names_var.get())
        entry_names.grid(row=1, column=1, sticky="WE", padx=10)
        entry_names.bind("<Return>", insert_comma)

        button_frame = ttk.Frame(window)
        button_frame.grid(row=2, column=1, sticky="W", padx=10, pady=10)

        button_clear = ttk.Button(button_frame, text=self._("Clear"), command=clear_text)
        button_clear.grid(row=0, column=0, sticky="W", padx=10)

        button_import = ttk.Button(button_frame, text=self._("Import"), command=import_names)
        button_import.grid(row=0, column=1, sticky="W", padx=10)

        label_highlight_mode = ttk.Label(window, text=self._("Highlight Mode"))
        label_highlight_mode.grid(row=3, column=0, sticky="W", padx=10)

        radio_highlight_only = ttk.Radiobutton(
            window,
            text=self._("Highlight lines with matched names in blue, others are not highlighted"),
            variable=temp_highlight_mode_var,
            value=HighlightMode.ONLY_NAMES.name,
        )
        radio_highlight_only.grid(row=3, column=1, sticky="W", padx=10)

        radio_highlight_color = ttk.Radiobutton(
            window,
            text=self._("Highlight lines with matched names in blue, others in yellow"),
            variable=temp_highlight_mode_var,
            value=HighlightMode.NAMES_DIFF_COLOR.name,
        )
        radio_highlight_color.grid(row=4, column=1, sticky="W", padx=10)

        button_frame2 = ttk.Frame(window)
        button_frame2.grid(row=5, column=0, columnspan=2, sticky="WE", padx=10, pady=10)

        button_apply = ttk.Button(button_frame2, text=self._("Apply"), command=apply_changes)
        button_apply.pack(side="left", padx=10, expand=True)

        button_abort = ttk.Button(button_frame2, text=self._("Cancel"), command=window.destroy)
        button_abort.pack(side="right", padx=10, expand=True)

    # Update open_watermark_window (remove opacity; add preview page spinbox and color preselects):
    def open_watermark_window(self):
        window = Toplevel(self.root)
        window.title(self._("Watermark Settings"))
        # Removed window.grab_set() to allow closing/minimizing preview window
        window.focus_set()
        # Removed preview page input since navigation buttons now handle page changes
        temp_enabled = IntVar(value=1 if self.app_settings.settings.get("watermark_enabled") == "True" else 0)
        temp_text = StringVar(value=self.app_settings.settings.get("watermark_text"))
        temp_color = StringVar(value=self.app_settings.settings.get("watermark_color"))
        temp_size = StringVar(value=str(self.app_settings.settings.get("watermark_size")))
        temp_position = StringVar(value=self.app_settings.settings.get("watermark_position"))
        Label(window, text=self._("Enable Watermark")).grid(row=0, column=0, sticky="W", padx=10, pady=5)
        chk = ttk.Checkbutton(window, variable=temp_enabled)
        chk.grid(row=0, column=1, sticky="W", padx=10, pady=5)
        Label(window, text=self._("Watermark Text")).grid(row=1, column=0, sticky="W", padx=10, pady=5)
        entry_text = ttk.Entry(window, textvariable=temp_text)
        entry_text.grid(row=1, column=1, padx=10, pady=5)
        Label(window, text=self._("Color (hex)")).grid(row=2, column=0, sticky="W", padx=10, pady=5)
        entry_color = ttk.Entry(window, textvariable=temp_color)
        entry_color.grid(row=2, column=1, padx=10, pady=5)
        # Preselect color frame remains unchanged...
        preselect_frame = ttk.Frame(window)
        preselect_frame.grid(row=3, column=0, columnspan=2, padx=10, pady=5, sticky="W")
        Label(preselect_frame, text=self._("Preselect Color:")).pack(side="left")

        preset_colors = ["#FFA500", "#FF0000", "#00FF00", "#0000FF"]
        preselect_buttons = {}

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
        Label(window, text=self._("Size")).grid(row=4, column=0, sticky="W", padx=10, pady=5)
        entry_size = ttk.Spinbox(window, from_=1, to=100, textvariable=temp_size, width=5)
        entry_size.grid(row=4, column=1, padx=10, pady=5, sticky="W")
        Label(window, text=self._("Position")).grid(row=5, column=0, sticky="W", padx=10, pady=5)
        # Allow only "top" and "bottom"
        position_options = ["top", "bottom"]
        option_position = ttk.OptionMenu(window, temp_position, temp_position.get(), *position_options)
        option_position.grid(row=5, column=1, padx=10, pady=5, sticky="W")

        # Remove preview page input
        def preview(force_open=True):
            # Use the current preview page (kept in self.current_preview_page)
            self.preview_watermark(
                temp_enabled.get(),
                temp_text.get(),
                temp_color.get(),
                int(temp_size.get()),
                temp_position.get(),
                self.current_preview_page,
                origin=window,
                force_open=force_open,
            )

        # Dynamic update: only update if preview window exists (do not open one)
        def update_preview(*args):
            preview(force_open=False)

        temp_enabled.trace_add("write", update_preview)
        temp_text.trace_add("write", update_preview)
        temp_color.trace_add("write", update_preview)
        temp_size.trace_add("write", update_preview)
        temp_position.trace_add("write", update_preview)
        btn_preview = ttk.Button(window, text=self._("Preview"), command=lambda: preview(force_open=True))
        btn_preview.grid(row=7, column=0, columnspan=2, pady=10)

        def apply_changes():
            self.app_settings.update_setting("watermark_enabled", "True" if temp_enabled.get() else "False")
            self.app_settings.update_setting("watermark_text", temp_text.get())
            self.app_settings.update_setting("watermark_color", temp_color.get())
            self.app_settings.update_setting("watermark_size", int(temp_size.get()))
            self.app_settings.update_setting("watermark_position", temp_position.get())
            window.destroy()

        btn_apply = ttk.Button(window, text=self._("Apply"), command=apply_changes)
        btn_apply.grid(row=8, column=0, pady=10, padx=10, sticky="E")
        btn_cancel = ttk.Button(window, text=self._("Cancel"), command=window.destroy)
        btn_cancel.grid(row=8, column=1, pady=10, padx=10, sticky="W")

    def preview_watermark(self, enabled, text, color, size, position, preview_page, origin=None, force_open=True):
        if not hasattr(self, "input_file_full_path"):
            messagebox.showerror(self._("Error"), self._("Please select a PDF first for preview."))
            return
        try:
            document = Document(self.input_file_full_path)
            if preview_page == len(document) + 1:
                self.change_preview_page(-1) # Go back to last page
                document.close()
                return
            elif preview_page > len(document) or preview_page < 1:
                self.change_preview_page(0, reset=True)  # Reset to first page
                document.close()
            page: utils = document[preview_page - 1]
            pix: Pixmap = page.get_pixmap()

            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            if enabled and text:
                image = overlay_watermark_on_image(image, text, size, color, position)

            # Save last settings and origin for dynamic updates and navigation
            self.last_watermark_data = {"enabled": enabled, "text": text, "color": color, "size": size, "position": position}
            self.last_preview_origin = origin if origin else self.root

            # If preview_window exists, update image; otherwise, only open window if force_open is True.
            if self.preview_window and self.preview_window.winfo_exists():
                self.preview_window.lift()
                if force_open:
                    self.preview_window.focus_set()  # Only force focus when preview is explicitly opened
                img_tk = ImageTk.PhotoImage(image)
                # Find and update the image label without changing widget focus.
                for widget in self.preview_window.winfo_children():
                    if isinstance(widget, Label):
                        widget.configure(image=img_tk)
                        widget.image = img_tk  # keep reference
                        break
            else:
                if force_open:
                    self.preview_window = Toplevel()
                    self.preview_window.title(self._("Watermark Preview"))
                    # Position next to the origin window
                    ox = self.last_preview_origin.winfo_rootx()
                    oy = self.last_preview_origin.winfo_rooty()
                    ow = self.last_preview_origin.winfo_width()
                    self.preview_window.geometry(f"+{ox + ow + 10}+{oy}")
                    self.preview_window.transient(None)
                    self.preview_window.grab_release()
                    self.preview_window.protocol("WM_DELETE_WINDOW", self.preview_window.destroy)
                    img_tk = ImageTk.PhotoImage(image)
                    img_label = Label(self.preview_window, image=img_tk)
                    img_label.image = img_tk  # keep reference
                    img_label.pack()
                    # Navigation buttons frame
                    nav_frame = ttk.Frame(self.preview_window)
                    nav_frame.pack(pady=5)
                    prev_btn = ttk.Button(nav_frame, text=self._("Previous Page"), command=lambda: self.change_preview_page(-1))
                    prev_btn.pack(side="left", padx=5)
                    next_btn = ttk.Button(nav_frame, text=self._("Next Page"), command=lambda: self.change_preview_page(1))
                    next_btn.pack(side="left", padx=5)
            document.close()
        except Exception as e:
            messagebox.showerror(self._("Error"), str(e))

    # New method to change preview page
    def change_preview_page(self, delta: int, reset: bool = False):
        if reset:
            self.current_preview_page = 1
        else:
            self.current_preview_page = max(1, self.current_preview_page + delta)
        # Re-call preview_watermark with stored settings
        if self.last_watermark_data:
            self.preview_watermark(
                self.last_watermark_data["enabled"],
                self.last_watermark_data["text"],
                self.last_watermark_data["color"],
                self.last_watermark_data["size"],
                self.last_watermark_data["position"],
                self.current_preview_page,
                origin=self.last_preview_origin,
            )

    def update_version_labels_text(self, latest_version: Version, current_version: Version = Version.from_str(VERSION_STR)):
        self.init_translatable_strings_version()
        if latest_version is None or latest_version is False:
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
        self.checkbox_relevant_lines.config(text=self._("Mark only relevant lines"))
        self.status_var.set(self._("Status: Language changed to English."))
        self.start_abort_button.config(text=self._("Start"))
        self.browse_button.config(text=self._("Browse"))
        self.title.config(text=self._("Heat sheet highlighter"))
        self.language_menu.config(text=self._("Select language"))
        self.root.title(self._("Heat sheet highlighter"))
        self.button_filter.config(text=self._("Filter"))
        self.button_watermark.config(text=self._("Watermark"))
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
        Tooltip(self.button_filter, text=self._("Configure highlighting lines with specific names."))
        Tooltip(self.button_watermark, text=self._("Configure watermark options."))
        Tooltip(self.start_abort_button, text=self._("Start or cancel the highlight process."))
        Tooltip(self.language_menu, text=self._("Select the language"))

    def browse_file(self):
        """
        Opens a file dialog to browse and select a PDF file.
        """
        self.status_var.set(self._("Status: Importing PDF. Please wait..."))
        self.root.update_idletasks()
        file_path = filedialog.askopenfilename(filetypes=((self._("PDF files"), "*.pdf"), (self._("All files"), "*.*")))
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
        self.start_abort_button.config(text=self._("Abort"), command=self.finalize_processing)

        # Set processing flag to True
        self.processing_active = True

        # update the search string in the settings
        if self.search_phrase_var.get() != self.app_settings.settings["search_str"]:
            self.app_settings.update_setting("search_str", self.search_phrase_var.get())

        # Start the processing in a separate thread
        input_file = getattr(self, "input_file_full_path", None)
        search_str = self.search_phrase_var.get()

        if not all([input_file, search_str]):
            messagebox.showerror(self._("Error"), self._("All fields are required!"))
            self.finalize_processing()
            return

        threading.Thread(target=self.process_pdf, args=(input_file,), daemon=True).start()

    def process_pdf(self, input_file: str):
        self.processing_active = True
        search_str = self.search_phrase_var.get()
        only_relevant = bool(self.relevant_lines_var.get())
        filter_enabled = bool(self.enable_filter_var.get())
        highlight_mode = HighlightMode[self.highlight_mode_var.get()]
        names = [name.strip() for name in re.split(r",\s*", self.names_var.get()) if name.strip()]

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
        self.progress_bar["value"] = 0  # Reset progress bar
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
        self.progress_bar["value"] = (current / total) * 100
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
            latest_version = Version.from_str(release_info["tag_name"])
            download_url = release_info["assets"][0]["browser_download_url"]

            if self.app_settings.settings["beta"]:
                release_url = "https://api.github.com/repos/jonalbr/heat-sheet-pdf-highlighter/releases"

                # Send GET request to GitHub API
                response = requests.get(release_url)
                response.raise_for_status()

                # Parse the response JSON
                releases_info = response.json()

                # Filter the releases to only include pre-releases
                pre_releases = [release for release in releases_info if release["prerelease"]]

                # If there are no pre-releases, return None or handle accordingly
                if pre_releases:
                    # Get the latest pre-release (the first one in the list as GitHub returns them in reverse chronological order)
                    latest_pre_release = pre_releases[0]

                    # Get the latest pre-release version number
                    latest_pre_release_version = Version.from_str(latest_pre_release["tag_name"])

                    # If the latest pre-release is newer than the latest release, update the latest version and download URL
                    if latest_pre_release_version > latest_version:
                        latest_version = latest_pre_release_version
                        download_url = latest_pre_release["assets"][0]["browser_download_url"]
                        self.app_settings.update_setting("newest_version_available", str(latest_version))
                        self.app_settings.update_setting("ask_for_update", True)
                else:
                    self.app_settings.update_setting("newest_version_available", str(latest_version))
                    self.app_settings.update_setting("ask_for_update", True)

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
                    self._("A new version ({0}) is available. Do you want to update?").format(latest_version),
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
            response = requests.get(download_url, stream=True)
            response.raise_for_status()

            total_size_in_bytes = int(response.headers.get("content-length", 0))
            block_size = 1024  # 1 KB

            self.progress_bar["maximum"] = total_size_in_bytes
            start_time = time.time()

            with open(installer_path, "wb") as file:
                last_update_time = time.time()
                for data in response.iter_content(block_size):
                    file.write(data)
                    self.progress_bar["value"] += len(data)  # Update the progress bar's value
                    current_time = time.time()
                    if current_time - last_update_time >= 0.25:  # Update the GUI every 1/4 second
                        self.update_progress_bar(start_time, total_size_in_bytes)  # Call the method directly
                        last_update_time = current_time

            if total_size_in_bytes != 0 and self.progress_bar["value"] != total_size_in_bytes:
                print("ERROR, something went wrong")

        except requests.exceptions.HTTPError as e:
            messagebox.showerror(self._("Error"), self._("Failed to download the installer: {0}").format(str(e)))

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

    def update_progress_bar(self, start_time, total_size_in_bytes):
        elapsed_time = time.time() - start_time
        speed = self.progress_bar["value"] / elapsed_time
        remaining_time = (total_size_in_bytes - self.progress_bar["value"]) / speed
        downloaded_MB = self.progress_bar["value"] / (1024 * 1024)
        total_MB = total_size_in_bytes / (1024 * 1024)
        self.status_var.set(
            self._("Downloading... {0:.1f} MB of {1:.1f} MB, {2:.0f} seconds remaining").format(downloaded_MB, total_MB, remaining_time)
        )
        self.root.update()  # Update the GUI


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
