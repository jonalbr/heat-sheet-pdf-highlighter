"""
UI strings module for the GUI.

This module exposes a build_strings function which accepts the translation
callable `_` and returns the translated strings dict. It also exposes the
raw plural_strings data which should be passed to ngettext at runtime.
"""

from typing import Callable, Dict


def build_strings(_: Callable[[str], str]) -> Dict[str, str]:
    """Return a dict of translated UI strings using the provided `_`.

    Args:
        _: The gettext translation function (usually named `_`).

    Returns:
        A dictionary mapping string keys to translated values.
    """
    general_strings = {
        "title": _("Heat sheet highlighter"),
        "pdf_file": _("PDF-File:"),
        "search_term": _("Search term (Club name):"),
        "mark_only_relevant": _("Mark only relevant lines"),
        "status_waiting": _("Status: Waiting"),
        "status_importing": _("Status: Importing PDF. Please wait..."),
        "status_imported": _("Status: PDF imported."),
        "status_language_changed": _("Status: Language changed to English."),
        "start": _("Start"),
        "abort": _("Abort"),
        "browse": _("Browse"),
        "filter": _("Filter"),
        "watermark": _("Watermark"),
        "select_language": _("Select language"),
        "select_pdf": _("Select the heat sheet pdf."),
        "enter_club": _("Enter the name of the club to highlight the results."),
        "only_highlight_lines": _(
            "Only highlights the lines that contain the search term and match the expected format (Lane Name ... Time).\nYou usually want to keep that enabled."
        ),
        "configure_filter": _("Configure highlighting lines with specific names."),
        "configure_watermark": _("Configure the watermark options."),
        "start_cancel": _("Start or cancel the highlighting process."),
        "error": _("Error"),
        "info": _("Info")
    }
    update_strings = {
        # Version/update related
        "version_update_failed": _("Version: {0} (Update check failed)"),
        "version_new_available": _("Version: {0} (New version available)"),
        "version_no_update": _("Version: {0}"),
        "check_for_updates": _("Check for Updates"),
        "install_update": _("Install Update"),
    }
    additional_strings = {
        "PDF files": _("PDF files"),
        "All files": _("All files"),
        "CSV and Text files": _("CSV and Text files"),
        "Previous Page": _("Previous Page"),
        "Next Page": _("Next Page"),
        "Watermark Preview": _("Watermark Preview"),
        "Watermark Settings": _("Watermark Settings"),
        "Enable Watermark": _("Enable Watermark"),
        "Watermark Text": _("Watermark Text"),
        "Color (hex)": _("Color (hex)"),
        "Preselect Color:": _("Preselect Color:"),
        "Size": _("Size"),
        "Position": _("Position"),
        "Preview": _("Preview"),
        "Apply": _("Apply"),
        "Cancel": _("Cancel"),
        "Clear": _("Clear"),
        "Import": _("Import"),
        "Names": _("Names"),
        "Enable Filter": _("Enable Filter"),
        "Highlight Mode": _("Highlight Mode"),
        "Highlight lines with matched names in blue, others are not highlighted": _(
            "Highlight lines with matched names in blue, others are not highlighted"
        ),
        "Highlight lines with matched names in blue, others in yellow": _("Highlight lines with matched names in blue, others in yellow"),
        "Enable highlighting lines with specific names.": _("Enable highlighting lines with specific names."),
        "Password-protected PDFs are not supported.": _("Password-protected PDFs are not supported."),
        "Status: Saving PDF.. Please wait...": _("Status: Saving PDF.. Please wait..."),
        "{0}_marked.pdf": _("{0}_marked.pdf"),
        "Finished": _("Finished"),
        "No output file selected; processing aborted after matches were found.": _(
            "No output file selected; processing aborted after matches were found."
        ),
        "Nothing to highlight; no file saved.": _("Nothing to highlight; no file saved."),
        "Status: Aborted by user.": _("Status: Aborted by user."),
        "Status: Processing aborted.": _("Status: Processing aborted."),
        "All fields are required!": _("All fields are required!"),
        "Please select a PDF first for preview.": _("Please select a PDF first for preview."),
        # Update-related strings
        "Up to Date": _("Up to Date"),
        "You are already using the latest version.": _("You are already using the latest version."),
        "Update Available": _("Update Available"),
        "A new version ({0}) is available. Do you want to update?": _("A new version ({0}) is available. Do you want to update?"),
        "Update Information": _("Update Information"),
        "Click 'yes' to not be asked again for this update. You can still check manually for updates. If there is a newer version available, you will be asked again.": _(
            "Click 'yes' to not be asked again for this update. You can still check manually for updates. If there is a newer version available, you will be asked again."
        ),
        "Update Error": _("Update Error"),
        "Failed to check for updates: {0}": _("Failed to check for updates: {0}"),
        "Failed to download the installer: {0}": _("Failed to download the installer: {0}"),
        "Downloading... {0:.1f} MB of {1:.1f} MB, {2:.0f} seconds remaining": _("Downloading... {0:.1f} MB of {1:.1f} MB, {2:.0f} seconds remaining"),
        "Download cancelled.": _("Download cancelled."),
    }
    dev_strings = {
        "Dev Tools": _("Dev Tools"),
        "Update Channel": _("Update Channel"),
        "Stable": _("Stable"),
        "Release Candidates (rc)": _("Release Candidates (rc)"),
        "Debug Options": _("Debug Options"),
        "More tools coming soon…": _("More tools coming soon…"),
        "Close": _("Close"),
    }
    strings = general_strings | update_strings | additional_strings | dev_strings

    return strings


# Plural strings - keep as raw strings for proper ngettext handling
plural_strings = {
    "processing_complete": {
        "singular": "Processing complete: {0} match found. {1} skipped.",
        "plural": "Processing complete: {0} matches found. {1} skipped.",
    },
    "processed_pages": {
        "singular": "Processed: {0}/{1} pages. {2} match found. {3} skipped.",
        "plural": "Processed: {0}/{1} pages. {2} matches found. {3} skipped.",
    },
}

# xgettext hint - never executed, only for extraction
def _xgettext_dummy(n_):
    # Keep the ngettext patterns in source for translation extraction tools
    n_("Processing complete: {0} match found. {1} skipped.",
       "Processing complete: {0} matches found. {1} skipped.", 1)
    n_("Processed: {0}/{1} pages. {2} match found. {3} skipped.",
       "Processed: {0}/{1} pages. {2} matches found. {3} skipped.", 1)
    