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
    logging.getLogger("ui_strings").warning("Missing translation key: %s", key)
    return f"Error: Key missing: {key}"


def build_strings(_: Callable[[str], str]) -> Dict[str, str]:
    """Return a dict of translated UI strings using the provided `_`."""
    # A position's "Custom" has a different German label from the color picker.
    _p = getattr(getattr(_, "__self__", None), "pgettext", lambda context, text: text)

    # ---- General app text / statuses shown broadly ----
    general_strings = {
        "opt_active": _("Active options:"),
        "opt_job": _("Job options:"),
        "opt_all": _("All matches"),
        "opt_distinguish": _("Distinguish names"),
        "opt_named": _("Only listed names"),
        "opt_relevant": _("Relevant lines only"),
        "opt_any_line": _("All lines"),
        "opt_watermark_on": _("Watermark on"),
        "opt_watermark_off": _("Watermark off"),
        "opt_watermark_empty": _("Watermark has no text"),
        "opt_ocr_auto": _("OCR on demand"),
        "opt_ocr_off": _("OCR off"),
        "result_title": _("Result"),
        "result_empty": _("No completed processing yet."),
        "result_location": _("Saved to: {0}"),
        "result_open_pdf": _("Open PDF"),
        "result_open_folder": _("Open folder"),
        "result_retry": _("Retry saving"),
        "result_choose": _("Choose another location"),
        "result_discard": _("Discard result"),
        "result_failed": _("Processing failed."),
        "result_processing_failed": _("The PDF could not be processed. Try again or select another PDF."),
        "result_save_failed": _("The PDF could not be saved. Your result is retained. Retry saving, choose another location or discard it."),
        "result_source_target": _("Choose a destination different from the source PDF."),
        "result_target_changed": _("The destination has changed since selection. Choose the location again to confirm overwriting."),
        "result_overwrite_title": _("Replace PDF?"),
        "result_overwrite": _("Replace the existing file after successful saving?\n{0}"),
        "result_open_failed": _("Could not open the saved result. You can find it at the location shown above."),
        "result_open_missing": _("The saved PDF is no longer at this location. Check whether it was moved or deleted."),
        "preview_failed": _("The PDF preview could not be displayed. Try again or select another PDF."),
        "settings_notice": _("Settings"),
        "settings_recovered": _(
            "The settings file could not be read as valid settings. Defaults are being used. "
            "An unchanged backup was saved to:\n{backup}"
        ),
        "settings_backup_failed": _(
            "The settings file contains damaged data, but a backup could not be saved. "
            "The original file will not be overwritten. Changes apply only to this session.\nFile: {path}"
        ),
        "settings_read_failed": _(
            "The settings file could not be accessed. Defaults are being used. "
            "The file will not be overwritten; changes apply only to this session.\nFile: {path}"
        ),
        "settings_write_failed": _(
            "Settings could not be saved. Your changes apply to this session. "
            "Saving will be retried when you change another setting.\nFile: {path}"
        ),
        "configure_filter": _("Configure name filtering and highlight colors."),
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
        "theme_menu_tooltip": _("Choose a theme mode: System, Light, or Dark."),
        "theme_system": _("System"),
        "theme_light": _("Light"),
        "theme_dark": _("Dark"),
        "status_importing": _("Status: Importing PDF. Please wait..."),
        "status_language_changed": _("Status: Language changed to English."),
        "status_waiting": _("Status: Waiting"),
        "title": _("Heat sheet highlighter"),
    }

    # ---- OCR ----
    ocr_strings = {
        "ocr_prompt_title": _("OCR Required"),
        "ocr_prompt": _(
            "This PDF looks like a scanned document, so its text is only an image.\n"
            "To find and highlight rows, the app must first read the image text with OCR.\n"
            "Without OCR, this document cannot be highlighted.\n"
            "OCR can take a while and may miss or misread some words."
        ),
        "ocr_prompt_more_info_show": _("▶ More info"),
        "ocr_prompt_more_info_hide": _("▼ Less info"),
        "ocr_prompt_details": _(
            "OCR means Optical Character Recognition. It adds a searchable text layer to scanned pages.\n"
            "The original scan stays visible, then the app searches the added text and places highlights.\n"
            "Results depend on scan quality, so check the output."
        ),
        "ocr_language_label": _("OCR language:"),
        "ocr_language_deu": _("German (recommended)"),
        "ocr_language_eng": _("English"),
        "ocr_language_deu_eng": _("German + English"),
        "ocr_reduction_failed": _("The PDF was saved, but its file size could not be reduced ({0:.1f} MB)."),
        "status_ocr_checking": _("Status: Checking PDF text..."),
        "status_ocr_needed": _("Status: PDF imported. OCR will be used when processing."),
        "status_ocr_processing": _("Status: Running OCR... {0}/{1} pages processed."),
        "val_ocr_assets_missing": _("Bundled OCR language data is missing. Please reinstall the app."),
    }

    # ---- Generic UI actions/buttons ----
    action_strings = {
        "btn_abort": _("Abort"),
        "btn_apply": _("Apply"),
        "btn_browse": _("Browse"),
        "btn_cancel": _("Cancel"),
        "btn_clear": _("Clear"),
        "btn_filter": _("Filter and Colors"),
        "btn_import": _("Import"),
        "btn_no": _("No"),
        "btn_ok": _("OK"),
        "btn_preview": _("Preview"),
        "btn_retry": _("Retry"),
        "btn_start": _("Start"),
        "btn_watermark": _("Watermark"),
        "btn_yes": _("Yes"),
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
        "nav_page_count": _("Page {0} of {1}"),
        "nav_go": _("Go to page"),
        "nav_fit": _("Fit to window"),
        "nav_invalid_page": _("Enter a page number from 1 to {0}."),
        "nav_no_pages": _("This PDF has no pages."),
        "nav_next": _("Next Page"),
        "nav_prev": _("Previous Page"),
    }

    # ---- Watermark dialog ----
    watermark_strings = {
        "wm_adjust": _("Adjust"),
        "wm_color": _("Color"),
        "wm_color_select": _("Choose"),
        "wm_custom_color": _("Custom"),
        "wm_enable": _("Enable Watermark"),
        "wm_pos": _("Position"),
        "wm_pos_top": _("Top"),
        "wm_pos_bottom": _("Bottom"),
        "wm_pos_custom": _p("watermark_position", "Custom"),
        "wm_invalid_color": _("Please choose a color in #RRGGBB format."),
        "wm_invalid_size": _("Please enter a whole number from 1 to 100 for the watermark size."),
        "wm_preview_window": _("Watermark Preview"),
        "wm_settings": _("Watermark Settings"),
        "wm_size": _("Size"),
        "wm_text": _("Watermark Text"),
    }

    # ---- Name filter dialog ----
    filter_strings = {
        "flt_all": _("Without name filter - highlight all matches"),
        "flt_distinguish": _("Highlight all matches, distinguish names"),
        "flt_named": _("Only names from the list"),
        "flt_names": _("Names"),
        "flt_names_required": _("Please enter at least one name for this mode."),
        "flt_preview_unavailable": _("Preview unavailable. Close and reopen this dialog to try again."),
        "flt_preview_single": _("Only one match category was found. Showing the available example."),
        "flt_preview_no_text": _("No searchable text is available. Showing a colour example."),
        "flt_preview_no_matches": _("No eligible search matches were found. Showing a colour example."),
        "flt_preview_named_page": _("Listed name — page {0}"),
        "flt_preview_other_page": _("Other search hit — page {0}"),
        "flt_preview_excerpts": _("PDF excerpts"),
        "flt_no_names_found": _(
            "No names from the list found for this search. Check the names or choose another marking mode."
        ),
        "flt_color_default": _("Default color"),
        "flt_color_names": _("Name color"),
        "flt_custom": _("Custom color..."),
        "flt_unused": _("Currently not used"),
        "flt_preview": _("Preview"),
        "flt_example": _("Example"),
        "flt_example_select_pdf": _("Example: Select a PDF to see a live preview"),
        "flt_example_heat": _("Heat {0}/{1} - approx. {2}"),
        "flt_example_lane": _("Lane"),
        "flt_example_swimmer": _("Swimmer"),
        "flt_example_year": _("YOB"),
        "flt_example_club": _("Club"),
        "flt_example_entry_time": _("Entry time"),
        "flt_example_lane_format": _("Lane {0}"),
        "flt_example_time_format": _("{minutes}:{seconds}.{hundredths}"),
        "flt_pdf_excerpt": _("PDF excerpt - page {0}"),
        "flt_yellow": _("Yellow"),
        "flt_turquoise": _("Turquoise"),
        "flt_green": _("Green"),
        "flt_orange": _("Orange"),
        "flt_pink": _("Pink"),
        "flt_violet": _("Violet"),
    }

    # ---- Process/status lines outside of the general pool ----
    status_strings = {
        "status_processing": _("Status: Processing PDF…"),
        "btn_cancelling": _("Cancelling …"),
        "status_cancelling": _("Status: Cancelling …"),
        "status_preparing": _("Status: Preparing PDF processing…"),
        "status_aborted": _("Status: Aborted by user."),
        "status_aborted_processing": _("Status: Processing aborted."),
        "status_done": _("Finished"),
        "status_saving": _("Status: Saving PDF... Please wait..."),
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
        "upd_check_failed": _("Could not check for updates. Please try again."),
        "upd_download_failed": _("The update could not be completed. Please try again later."),
        "upd_error": _("Update Error"),
        "upd_latest": _("You are already using the latest version."),
        "upd_now": _("Update now"),
        "upd_later": _("Later"),
        "upd_skip_version": _("Do not ask again for this version"),
        "upd_ok": _("Up to Date"),
        "upd_progress": _("Downloading... {0:.1f} MB of {1:.1f} MB, {2:.0f} seconds remaining"),
        "upd_starting": _("Starting download..."),
        "upd_prompt": _("A new version ({0}) is available."),
    }

    # ---- Developer tools ----
    dev_strings = {
        "dev_versions_empty": _("No versions are available for this channel."),
        "dev_versions_loading": _("Loading versions..."),
        "dev_versions_failed": _("Could not load versions. Please try again."),
        "dev_versions_cached": _("Could not refresh versions. Previously loaded versions are still available."),
        "dev_checksum_missing": _("This version has no checksum file. Choose another version to keep verification enabled."),
        "dev_settings_open_failed": _("Could not open the settings file. You can find it here: {0}"),
        "dev_btn_install": _("Install"),
        "dev_channel": _("Channel"),
        "dev_confirm_install": _("Are you sure you want to install version {0}?"),
        "dev_confirm_reset": _("Are you sure you want to reset all settings to defaults?"),
        "dev_debug": _("Debug Options"),
        "dev_install_specific": _("Install Specific Version"),
        "dev_open_settings": _("Open Settings File"),
        "dev_rc": _("Release Candidates (rc)"),
        "dev_release_notes": _("Release Notes"),
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
        | ocr_strings
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
    "option_names": {
        "singular": "{0} name",
        "plural": "{0} names",
    },
    "marked_lines": {
        "singular": "{0} line newly highlighted.",
        "plural": "{0} lines newly highlighted.",
    },
    "watermarked_pages": {
        "singular": "Watermark applied to {0} page.",
        "plural": "Watermark applied to {0} pages.",
    },
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
    n_("{0} name", "{0} names", 1)
    n_("{0} line newly highlighted.", "{0} lines newly highlighted.", 1)
    n_("Watermark applied to {0} page.", "Watermark applied to {0} pages.", 1)
    n_("Processing complete: {0} match found. {1} skipped.", "Processing complete: {0} matches found. {1} skipped.", 1)
    n_("Processed: {0}/{1} pages. {2} match found. {3} skipped.", "Processed: {0}/{1} pages. {2} matches found. {3} skipped.", 1)
