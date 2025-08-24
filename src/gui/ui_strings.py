"""
UI strings module for the GUI.

This module exposes a build_strings function which accepts the translation
callable `_` and returns the translated strings dict. It also exposes the
raw plural_strings data which should be passed to ngettext at runtime.
"""

import logging
from typing import Callable, Dict


def get_ui_string(strings: Dict[str, str], key: str, default: str | None = None) -> str:
    """Safe string lookup with logging and a diagnostic fallback."""
    if key in strings:
        return strings[key]
    if default is not None:
        return default
    try:
        logging.getLogger("ui_strings").warning("Missing translation key: %s", key)
    except Exception:
        pass
    return f"Error: Key missing: {key}"


def build_strings(_: Callable[[str], str]) -> Dict[str, str]:
    """Return a dict of translated UI strings using the provided `_`."""

    # ---- General app text / statuses shown broadly ----
    general_strings = {
        "configure_filter": _("Configure highlighting lines with specific names."),
        "configure_watermark": _("Configure the watermark options."),
        "enter_club": _("Enter the name of the club to highlight the results."),
        "error": _("Error"),
        "info": _("Info"),
        "mark_only_relevant": _("Mark only relevant lines"),
        "only_highlight_lines": _(
            "Only highlights the lines that contain the search term and match the expected format (Lane Name ... Time).\n"
            "You usually want to keep that enabled."
        ),
        "pdf_file": _("PDF-File:"),
        "search_term": _("Search term (Club name):"),
        "select_language": _("Select language"),
        "select_pdf": _("Select the heat sheet pdf."),
        "start_cancel": _("Start or cancel the highlighting process."),
        "status_imported": _("Status: PDF imported."),
        "status_importing": _("Status: Importing PDF. Please wait..."),
        "status_language_changed": _("Status: Language changed to English."),
        "status_waiting": _("Status: Waiting"),
        "title": _("Heat sheet highlighter"),
    }

    # ---- Generic UI actions/buttons ----
    action_strings = {
        "btn_abort": _("Abort"),
        "btn_apply": _("Apply"),
        "btn_browse": _("Browse"),
        "btn_cancel": _("Cancel"),
        "btn_clear": _("Clear"),
        "btn_filter": _("Filter"),
        "btn_import": _("Import"),
        "btn_preview": _("Preview"),
        "btn_start": _("Start"),
        "btn_watermark": _("Watermark"),
    }

    # ---- File pickers, filters, and output naming ----
    file_strings = {
        "file_filter_all": _("All files"),
        "file_filter_csv": _("CSV and Text files"),
        "file_filter_pdf": _("PDF files"),
        "file_out_pattern": _("{0}_marked.pdf"),
    }

    # ---- Pagination / navigation bits ----
    nav_strings = {
        "nav_next": _("Next Page"),
        "nav_prev": _("Previous Page"),
    }

    # ---- Watermark dialog ----
    watermark_strings = {
        "wm_color_hex": _("Color (hex)"),
        "wm_enable": _("Enable Watermark"),
        "wm_pos": _("Position"),
        "wm_pre_color": _("Preselect Color:"),
        "wm_preview_window": _("Watermark Preview"),
        "wm_settings": _("Watermark Settings"),
        "wm_size": _("Size"),
        "wm_text": _("Watermark Text"),
    }

    # ---- Name filter dialog ----
    filter_strings = {
        "flt_enable": _("Enable Filter"),
        "flt_info": _("Enable highlighting lines with specific names."),
        "flt_mode": _("Highlight Mode"),
        "flt_mode_blue": _("Highlight lines with matched names in blue, others are not highlighted"),
        "flt_mode_blue_yellow": _("Highlight lines with matched names in blue, others in yellow"),
        "flt_names": _("Names"),
    }

    # ---- Process/status lines outside of the general pool ----
    status_strings = {
        "status_aborted": _("Status: Aborted by user."),
        "status_aborted_processing": _("Status: Processing aborted."),
        "status_done": _("Finished"),
        "status_saving": _("Status: Saving PDF.. Please wait..."),
    }

    # ---- Validation and user-facing errors ----
    validation_strings = {
        "val_all_required": _("All fields are required!"),
        "val_no_output": _("No output file selected; processing aborted after matches were found."),
        "val_nothing": _("Nothing to highlight; no file saved."),
        "val_pdf_first": _("Please select a PDF first for preview."),
        "val_pdf_protected": _("Password-protected PDFs are not supported."),
    }

    # ---- Version line (footer/status text) ----
    version_strings = {
        "ver_new": _("Version: {0} (New version available)"),
        "ver_no_update": _("Version: {0}"),
        "ver_update_failed": _("Version: {0} (Update check failed)"),
    }

    # ---- Update actions (buttons/entry points) ----
    update_action_strings = {
        "upd_check": _("Check for Updates"),
        "upd_install": _("Install Update"),
    }

    # ---- Update dialogs / notifications ----
    update_ui_strings = {
        "upd_avail": _("Update Available"),
        "upd_cancelled": _("Download cancelled."),
        "upd_check_failed": _("Failed to check for updates: {0}"),
        "upd_download_failed": _("Failed to download the installer: {0}"),
        "upd_error": _("Update Error"),
        "upd_info": _("Update Information"),
        "upd_latest": _("You are already using the latest version."),
        "upd_note": _(
            "Click 'yes' to not be asked again for this update. You can still check manually for updates. "
            "If there is a newer version available, you will be asked again."
        ),
        "upd_ok": _("Up to Date"),
        "upd_progress": _("Downloading... {0:.1f} MB of {1:.1f} MB, {2:.0f} seconds remaining"),
        "upd_prompt": _("A new version ({0}) is available. Do you want to update?"),
    }

    # ---- Developer tools ----
    dev_strings = {
        "dev_channel": _("Channel"),
        "dev_confirm_reset": _("Are you sure you want to reset all settings to defaults?"),
        "dev_debug": _("Debug Options"),
        "dev_install_selected": _("Install Selected"),
        "dev_install_specific": _("Install Specific Version"),
        "dev_open_settings": _("Open Settings File"),
        "dev_rc": _("Release Candidates (rc)"),
        "dev_reset_defaults": _("Reset to Defaults"),
        "dev_reset_settings": _("Reset Settings"),
        "dev_sha256_info": _("When enabled, updates require a matching .sha256 file for verification (affects all channels)"),
        "dev_stable": _("Stable"),
        "dev_tools": _("Dev Tools"),
        "dev_update_channel": _("Update Channel"),
        "dev_verify_sha256": _("Verify SHA256"),
    }

    # Merge (order preserved; keys sorted within each dict)
    strings = (
        general_strings
        | action_strings
        | file_strings
        | nav_strings
        | watermark_strings
        | filter_strings
        | status_strings
        | validation_strings
        | version_strings
        | update_action_strings
        | update_ui_strings
        | dev_strings
    )

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
    n_("Processing complete: {0} match found. {1} skipped.", "Processing complete: {0} matches found. {1} skipped.", 1)
    n_("Processed: {0}/{1} pages. {2} match found. {3} skipped.", "Processed: {0}/{1} pages. {2} matches found. {3} skipped.", 1)
