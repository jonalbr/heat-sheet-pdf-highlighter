"""
Developer Tools window
"""

import datetime
import threading
import webbrowser
from tkinter import BooleanVar, StringVar, Toplevel, messagebox, ttk
from typing import TYPE_CHECKING

from ..utils.cache import load_releases_cache, save_releases_cache
from ..version import Version
from .ui_strings import get_ui_string

if TYPE_CHECKING:
    from src.gui.main_window import PDFHighlighterApp
from .widgets import Tooltip


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
            except Exception:
                pass
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
        releases_frame.grid_columnconfigure(0, weight=1)

        ttk.Button(releases_frame, text=get_ui_string(self.app.strings, "dev_btn_install"), command=self._install_selected_release).grid(
            row=0, column=1, padx=8, pady=6, sticky="e"
        )

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
                except Exception:
                    pass
                # Re-open will recreate the window with updated strings
                self.open()
        except Exception:
            # Swallow exceptions to avoid disrupting language change flow
            return

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
        except Exception:
            return False

    # --- Extra actions ---
    def _open_settings_file(self):
        try:
            path = str(self.app.paths.settings_file)
            webbrowser.open(path)
        except Exception as e:
            messagebox.showerror(get_ui_string(self.app.strings, "error"), str(e))

    def _apply_releases(self, releases: list[dict]):
        try:
            tags = [r["tag"] for r in releases if r.get("exe_url")]
            self._releases_cache = {r["tag"]: r for r in releases}
            if tags:
                self.releases_combo["values"] = tags
                self.releases_combo.set(tags[0])
            else:
                self.releases_combo["values"] = []
                self.releases_combo.set("")
        except Exception as e:
            messagebox.showerror(get_ui_string(self.app.strings, "error"), str(e))

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
                            except Exception:
                                # protect from any race if widgets were destroyed
                                return

                    self.app.root.after(0, _schedule_apply)
                except Exception:
                    # if scheduling failed for any reason, ignore
                    return
            except Exception as _exc:
                # Use the main app root to show errors to avoid scheduling on a possibly-destroyed Toplevel
                try:
                    self.app.root.after(0, lambda e=_exc: messagebox.showerror(get_ui_string(self.app.strings, "error"), str(e)))
                except Exception:
                    # last resort: print to stderr (avoid crashing)
                    try:
                        import sys

                        print(str(_exc), file=sys.stderr)
                    except Exception:
                        pass

        threading.Thread(target=worker, daemon=True).start()

    def _start_refresh_releases_async(self, force: bool = False):
        """Increment refresh token and start async refresh worker (ignores stale results).

        If a cached releases file exists it will be applied immediately to populate the UI.
        A network fetch only runs if cache is stale (based on settings TTL) or if force=True.
        """
        self._releases_refresh_id += 1
        current_id = self._releases_refresh_id

        # Always try to apply cached releases immediately if present for the same channel
        try:
            fetched_at, cached_channel, cached_releases = load_releases_cache()
            if cached_releases and cached_channel == self.app.app_settings.settings.get("update_channel", "stable"):
                try:
                    self.app.root.after(0, lambda r=cached_releases: self._apply_releases(r))
                except Exception:
                    pass
        except Exception:
            pass

        # Decide whether to fetch from network based on TTL from settings
        try:
            ttl_seconds = int(self.app.app_settings.settings.get("releases_cache_ttl_seconds", 600))
        except Exception:
            ttl_seconds = 600

        need_fetch = force
        try:
            if not need_fetch:
                fetched_at, cached_channel, cached_releases = load_releases_cache()
                if not fetched_at or cached_channel != self.app.app_settings.settings.get("update_channel", "stable"):
                    need_fetch = True
                else:
                    age = (datetime.datetime.now() - fetched_at).total_seconds()
                    if age > ttl_seconds:
                        need_fetch = True
        except Exception:
            need_fetch = True

        if not need_fetch:
            return

        def worker_wrapper():
            try:
                channel = self.app.app_settings.settings.get("update_channel", "stable")
                releases = self.app.update_checker.list_releases(channel=channel)
                # persist releases to cache
                try:
                    save_releases_cache(releases=releases, channel=channel, fetched_at=datetime.datetime.now())
                except Exception:
                    pass

                def _schedule_apply(r=releases, rid=current_id):
                    # ignore stale results
                    if rid != self._releases_refresh_id:
                        return
                    if self._is_open():
                        try:
                            self._apply_releases(r)
                        except Exception:
                            return

                self.app.root.after(0, _schedule_apply)
            except Exception as _exc:
                try:
                    self.app.root.after(0, lambda e=_exc: messagebox.showerror(get_ui_string(self.app.strings, "error"), str(e)))
                except Exception:
                    try:
                        import sys

                        print(str(_exc), file=sys.stderr)
                    except Exception:
                        pass

        threading.Thread(target=worker_wrapper, daemon=True).start()

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
        sha_url = rel.get("sha_url") if self.sha_required.get() else None
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
