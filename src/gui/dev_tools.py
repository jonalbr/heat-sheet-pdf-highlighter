"""
Developer Tools window
"""

import datetime
import logging
import threading
import webbrowser
from tkinter import BooleanVar, StringVar, Toplevel, messagebox, ttk
from typing import TYPE_CHECKING

import markdown2
from tkinterweb import HtmlFrame

from ..utils.cache import load_releases_cache, save_releases_cache
from ..version import Version
from .ui_strings import get_ui_string
from .widgets import Tooltip

if TYPE_CHECKING:
    from src.gui.main_window import PDFHighlighterApp


class DevToolsWindow:
    """Secret Dev Tools panel for debugging and channel selection."""

    def __init__(self, app: "PDFHighlighterApp"):
        self.app = app
        self.window = None  # type: Toplevel | None
        self._releases_refresh_id = 0  # incremental id used to ignore stale async refresh results

    def open(self):
        if self.window and self._is_open():
            try:
                self.window.lift()
                self.window.focus_force()
            except Exception as e:
                logging.getLogger("dev_tools").exception("Error focusing dev tools window: %s", e)
            return

        self.window = Toplevel(self.app.root)
        self.window.title(get_ui_string(self.app.strings, "dev_tools"))
        self.window.transient(self.app.root)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)

        # --- Update Channel Section ---
        frame = ttk.LabelFrame(self.window, text=get_ui_string(self.app.strings, "dev_update_channel"))
        frame.grid(row=0, column=0, padx=12, pady=10, sticky="we")

        self.channel_var = StringVar()
        self.channel_var.set(self.app.app_settings.settings.get("update_channel", "stable"))
        options = [
            ("stable", get_ui_string(self.app.strings, "dev_stable")),
            ("rc", get_ui_string(self.app.strings, "dev_rc")),
        ]
        label_by_key = {k: lbl for k, lbl in options}
        key_by_label = {lbl: k for k, lbl in options}
        initial_label = label_by_key.get(self.channel_var.get(), "Stable")

        ttk.Label(frame, text=get_ui_string(self.app.strings, "dev_channel")).grid(row=0, column=0, padx=8, pady=6, sticky="w")
        combo = ttk.Combobox(frame, state="readonly", values=[lbl for _, lbl in options])
        combo.set(initial_label)
        combo.grid(row=0, column=1, padx=8, pady=6, sticky="we")

        # Global SHA verification toggle (applies to all installs)
        self.sha_required = BooleanVar(value=(self.app.app_settings.settings.get("verify_sha", "True") == "True"))
        sha_cb = ttk.Checkbutton(
            frame,
            text=get_ui_string(self.app.strings, "dev_verify_sha256"),
            variable=self.sha_required,
            command=lambda: self.app.app_settings.update_setting("verify_sha", "True" if self.sha_required.get() else "False"),
        )
        sha_cb.grid(row=0, column=2, padx=8, pady=6, sticky="w")
        Tooltip(
            sha_cb,
            text=get_ui_string(self.app.strings, "dev_sha256_info"),
        )
        frame.grid_columnconfigure(1, weight=1)

        def on_select(_evt=None):
            label = combo.get()
            channel = key_by_label.get(label, "stable")
            if channel != self.app.app_settings.settings.get("update_channel"):
                self.channel_var.set(channel)
                self._on_channel_changed()

        combo.bind("<<ComboboxSelected>>", on_select)

        # --- Placeholder for future debug options ---
        debug_frame = ttk.LabelFrame(self.window, text=get_ui_string(self.app.strings, "dev_debug"))
        debug_frame.grid(row=1, column=0, padx=12, pady=(0, 10), sticky="we")

        # Open settings file
        ttk.Button(debug_frame, text=get_ui_string(self.app.strings, "dev_open_settings"), command=self._open_settings_file).grid(
            row=0, column=0, padx=8, pady=6, sticky="w"
        )

        # Releases section
        releases_frame = ttk.LabelFrame(self.window, text=get_ui_string(self.app.strings, "dev_install_specific"))
        releases_frame.grid(row=2, column=0, padx=12, pady=(0, 10), sticky="we")

        # Refresh implicitly on open and on channel change
        self.releases_combo = ttk.Combobox(releases_frame, state="readonly", values=[])
        self.releases_combo.grid(row=0, column=0, padx=8, pady=6, sticky="we")
        # Update release notes when selection changes
        self.releases_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_release_notes())
        releases_frame.grid_columnconfigure(0, weight=1)

        ttk.Button(releases_frame, text=get_ui_string(self.app.strings, "dev_btn_install"), command=self._install_selected_release).grid(
            row=0, column=1, padx=8, pady=6, sticky="e"
        )

        # Collapsible Release Notes area (hidden by default)
        # Inline toggle: small triangle + label (clickable) to expand/collapse notes
        self._notes_toggle = ttk.Frame(releases_frame)
        # triangle label and text label
        self._notes_triangle_label = ttk.Label(self._notes_toggle, text="\u25b6")
        self._notes_text_label = ttk.Label(self._notes_toggle, text=get_ui_string(self.app.strings, "dev_release_notes"))
        self._notes_triangle_label.pack(side="left")
        self._notes_text_label.pack(side="left", padx=(4, 0))
        # Make it clickable like a button
        for widget in (self._notes_triangle_label, self._notes_text_label):
            widget.bind("<Button-1>", lambda _e: self._toggle_release_notes())
            widget.configure(cursor="hand2")
        self._notes_toggle.grid(row=0, column=2, padx=6, pady=6, sticky="e")

        self.notes_frame = ttk.Frame(releases_frame)

        # HTML/Markdown renderer
        self.notes_html = HtmlFrame(
            self.notes_frame,
            horizontal_scrollbar=False,
            vertical_scrollbar="auto",
            messages_enabled=False,
            shrink=False,
            fontscale=1.0,
            width=600,
            height=250,
        )
        self.notes_html.load_html("")
        self.notes_html.grid(row=0, column=0, sticky="nsew")
        self.notes_html.grid_propagate(False)

        self.notes_frame.grid_columnconfigure(0, weight=1)
        self.notes_frame.grid_rowconfigure(0, weight=1)
        # Hide notes by default
        self.release_notes_shown = False
        self.notes_frame.grid(row=1, column=0, columnspan=3, padx=8, pady=(4, 8), sticky="nsew")
        self.notes_frame.grid_remove()

        # Reset settings
        reset_frame = ttk.LabelFrame(self.window, text=get_ui_string(self.app.strings, "dev_reset_settings"))
        reset_frame.grid(row=3, column=0, padx=12, pady=(0, 10), sticky="we")
        ttk.Button(reset_frame, text=get_ui_string(self.app.strings, "dev_reset_defaults"), command=self._reset_settings).grid(
            row=0, column=0, padx=8, pady=6, sticky="w"
        )

        # Adjust grid without explicit Close button and populate releases initially
        self.window.grid_rowconfigure(3, weight=0)
        # Start async refresh (non-blocking)
        self._start_refresh_releases_async()

    def refresh_ui_strings(self):
        """Refresh UI strings for the dev tools window after language change.

        If the window is open, destroy and re-open it so all labels/strings are
        created using the new translations. If it's not open, nothing to do.
        """
        try:
            if self._is_open():
                try:
                    if self.window is not None:
                        self.window.destroy()
                except Exception as e:
                    logging.getLogger("dev_tools").exception("Error destroying dev tools window: %s", e)
                # Re-open will recreate the window with updated strings
                self.open()
        except Exception as e:
            logging.getLogger("dev_tools").exception("Error refreshing UI strings: %s", e)

    def _on_channel_changed(self):
        channel = self.channel_var.get()
        self.app.app_settings.update_setting("update_channel", channel)

        def _quiet_check():
            current = self.app_update_current_version()
            latest = self.app.update_checker.check_for_app_updates(current_version=current, force_check=True, quiet=True)
            self.app.update_version_labels_text(latest, current)
            self.app.update_version_labels()

        threading.Thread(target=_quiet_check, daemon=True).start()
        # And refresh releases to reflect channel (do this async so UI stays responsive)
        self._start_refresh_releases_async()

    def app_update_current_version(self):
        return Version.from_str(self.app.app_settings.settings["version"])

    def _is_open(self) -> bool:
        try:
            return bool(self.window and self.window.winfo_exists())
        except Exception as e:
            logging.getLogger("dev_tools").exception("Error checking if dev tools window is open: %s", e)
            return False

    # --- Extra actions ---
    def _open_settings_file(self):
        try:
            path = str(self.app.paths.settings_file)
            webbrowser.open(path)
        except Exception as e:
            logging.getLogger("dev_tools").exception("Error opening settings file: %s", e)
            messagebox.showerror(get_ui_string(self.app.strings, "error"), str(e))

    def _apply_releases(self, releases: list[dict]):
        try:
            tags = [r["tag"] for r in releases if r.get("exe_url")]
            self._releases_cache = {r["tag"]: r for r in releases}
            if tags:
                self.releases_combo["values"] = tags
                self.releases_combo.set(tags[0])
                # Update release notes for initially selected
                try:
                    self._update_release_notes()
                except Exception as e:
                    logging.getLogger("dev_tools").exception("Error updating release notes: %s", e)
            else:
                self.releases_combo["values"] = []
                self.releases_combo.set("")
        except Exception as e:
            logging.getLogger("dev_tools").exception("Error applying releases: %s", e)

    def _toggle_release_notes(self):
        """Show or hide the release notes area."""
        if self.release_notes_shown:
            # hide
            self.notes_frame.grid_remove()
            try:
                self._notes_triangle_label.config(text="\u25b6")
            except Exception as e:
                logging.getLogger("dev_tools").exception("Error updating notes triangle label: %s", e)
            self.release_notes_shown = False
        else:
            # show
            self.notes_frame.grid()
            try:
                self._notes_triangle_label.config(text="\u25bc")
            except Exception as e:
                logging.getLogger("dev_tools").exception("Error updating notes triangle label: %s", e)
            self.release_notes_shown = True

    def _update_release_notes(self):
        """Populate the notes_text from the selected release, using cache if available."""
        tag = self.releases_combo.get()
        if not tag:
            self._set_notes_text("")
            return
        rel = getattr(self, "_releases_cache", {}).get(tag)
        notes = ""
        if rel:
            # The releases dict from GitHub may include a "body" field with release notes
            notes = rel.get("body") or rel.get("notes") or ""
        # Fallback: if cache file contains releases, try to load more detailed body
        self._set_notes_text(notes)

    def _set_notes_text(self, text: str):
        try:
            html = self.md_to_html(text)
        except Exception as e:
            logging.getLogger("dev_tools").exception("Error converting markdown to HTML: %s", e)
            html = "<pre>" + (text or "") + "</pre>"
        try:
            # Load HTML into the HtmlFrame widget (tkinterweb handles CSS and headings)
            self.notes_html.load_html(html or "")
        except Exception as e:
            logging.getLogger("dev_tools").exception("Error setting HTML: %s", e)

    @staticmethod
    def md_to_html(md_text):
        # Enable extras that mirror GitHub-flavored Markdown features commonly used
        extras = [
            "fenced-code-blocks",
            "tables",
            "strike",
            "task_list",
            "toc",
            "header-ids",
            "break-on-newline",
            "code-friendly",
            "smarty-pants",
        ]
        return markdown2.markdown(md_text, extras=extras)

    def _refresh_releases_async(self):
        def worker():
            try:
                channel = self.app.app_settings.settings.get("update_channel", "stable")
                releases = self.app.update_checker.list_releases(channel=channel)
                # schedule UI update on main app root; the callback will re-check that
                # the Dev Tools window still exists before touching its widgets.
                try:

                    def _schedule_apply(r=releases):
                        if self._is_open():
                            try:
                                self._apply_releases(r)
                            except Exception as e:
                                logging.getLogger("dev_tools").exception("Error applying releases: %s", e)
                                # protect from any race if widgets were destroyed
                                return

                    self.app.root.after(0, _schedule_apply)
                except Exception as e:
                    logging.getLogger("dev_tools").exception("Error scheduling release application: %s", e)
                    # if scheduling failed for any reason, ignore
                    return
            except Exception as _exc:
                # Use the main app root to show errors to avoid scheduling on a possibly-destroyed Toplevel
                try:
                    self.app.root.after(0, lambda e=_exc: messagebox.showerror(get_ui_string(self.app.strings, "error"), str(e)))
                except Exception as e:
                    logging.getLogger("dev_tools").exception("Error showing error message: %s", e)

        threading.Thread(target=worker, daemon=True).start()

    def _start_refresh_releases_async(self, force: bool = False):
        """Increment refresh token and start async refresh worker (ignores stale results).

        If a cached releases file exists it will be applied immediately to populate the UI.
        A network fetch only runs if cache is stale (based on settings TTL) or if force=True.
        """
        self._releases_refresh_id += 1
        current_id = self._releases_refresh_id

        channel = self._get_channel()
        fetched_at, cached_channel, cached_releases = load_releases_cache()

        # Apply cached (if matches channel) to populate UI fast
        self._apply_cached_releases_if_channel_matches(cached_channel, cached_releases, channel)

        ttl_seconds = int(self.app.app_settings.settings.get("releases_cache_ttl_seconds", 600))
        if not self._should_fetch_releases(force, fetched_at, cached_channel, channel, ttl_seconds):
            return

        # Fetch and apply asynchronously with stale-guard
        threading.Thread(target=lambda: self._fetch_and_apply_releases_async(current_id), daemon=True).start()

    # --- Helpers to reduce complexity in _start_refresh_releases_async ---
    def _get_channel(self) -> str:
        return self.app.app_settings.settings.get("update_channel", "stable")

    def _apply_cached_releases_if_channel_matches(self, cached_channel: str | None, cached_releases: list[dict] | None, current_channel: str) -> None:
        if not cached_releases or cached_channel != current_channel:
            return
        try:
            self.app.root.after(0, lambda r=cached_releases: self._apply_releases(r))
        except Exception as e:
            logging.getLogger("dev_tools").exception("Error applying cached releases: %s", e)

    def _should_fetch_releases(
        self,
        force: bool,
        fetched_at: datetime.datetime | None,
        cached_channel: str | None,
        current_channel: str,
        ttl_seconds: int,
    ) -> bool:
        if force:
            return True
        if not fetched_at or cached_channel != current_channel:
            return True
        try:
            age = (datetime.datetime.now() - fetched_at).total_seconds()
            return age > ttl_seconds
        except Exception as e:
            logging.getLogger("dev_tools").exception("Error computing cache age: %s", e)
            return True

    def _fetch_and_apply_releases_async(self, refresh_id: int) -> None:
        try:
            channel = self._get_channel()
            releases = self.app.update_checker.list_releases(channel=channel)
            # persist releases to cache
            try:
                save_releases_cache(releases=releases, channel=channel, fetched_at=datetime.datetime.now())
            except Exception as e:
                logging.getLogger("dev_tools").exception("Error saving releases cache: %s", e)

            def _schedule_apply(r=releases, rid=refresh_id):
                if rid != self._releases_refresh_id:
                    return  # stale result, ignore
                if self._is_open():
                    try:
                        self._apply_releases(r)
                    except Exception as e:
                        logging.getLogger("dev_tools").exception("Error applying releases: %s", e)

            try:
                self.app.root.after(0, _schedule_apply)
            except Exception as e:
                logging.getLogger("dev_tools").exception("Error scheduling release application: %s", e)
        except Exception as _exc:
            self._show_error_async(_exc)

    def _show_error_async(self, exc: Exception) -> None:
        try:
            self.app.root.after(0, lambda e=exc: messagebox.showerror(get_ui_string(self.app.strings, "error"), str(e)))
        except Exception as e:
            logging.getLogger("dev_tools").exception("Error showing error message: %s", e)

    def _install_selected_release(self):
        tag = self.releases_combo.get()
        if not tag:
            return
        rel = getattr(self, "_releases_cache", {}).get(tag)
        if not rel or not rel.get("exe_url"):
            messagebox.showerror(
                get_ui_string(self.app.strings, "error"),
                get_ui_string(self.app.strings, "upd_download_failed").format("No installer asset"),
            )
            return
        exe_url = rel["exe_url"]
        verify_required = self.sha_required.get()
        sha_url = rel.get("sha_url") if verify_required else None
        # If verification is required globally but the release has no .sha256, block install to match updater policy
        if verify_required and not sha_url:
            messagebox.showerror(
                get_ui_string(self.app.strings, "error"),
                get_ui_string(self.app.strings, "upd_download_failed").format("Missing checksum (.sha256) for this release"),
            )
            return
        # Confirm install with the user
        if not messagebox.askokcancel(
            get_ui_string(self.app.strings, "dev_install_specific"),
            get_ui_string(self.app.strings, "dev_confirm_install").format(tag),
        ):
            return
        # Use existing updater flow to download and run installer
        threading.Thread(target=lambda: self.app.update_checker.download_and_run_installer(exe_url, sha_url), daemon=True).start()

    def _reset_settings(self):
        if not messagebox.askokcancel(
            get_ui_string(self.app.strings, "dev_reset_settings"),
            get_ui_string(self.app.strings, "dev_confirm_reset"),
        ):
            return
        self.app.app_settings.reset_to_defaults()
        # Re-apply language and refresh UI
        self.app.on_language_change(self.app.app_settings.settings["language"])
        # Update channel control
        self.channel_var.set(self.app.app_settings.settings.get("update_channel", "stable"))
        self._start_refresh_releases_async()
