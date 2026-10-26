"""Dev Tools with window-owned refreshes and a bounded, responsive layout."""

from contextlib import suppress
from dataclasses import dataclass
import datetime
import html
import logging
import os
import queue
import threading
import webbrowser
from tkinter import BooleanVar, Canvas, StringVar, TclError, Toplevel, ttk
from typing import TYPE_CHECKING

import markdown2
from tkinterweb import HtmlFrame

from ..utils.cache import load_releases_cache, save_releases_cache
from ..version import Version
from .dev_tools_geometry import (
    Bounds,
    apply_bounds,
    beside_parent,
    clamp_bounds,
    frame_padding,
    monitor_work_area,
    window_bounds,
)
from .message_dialog import ask_ok_cancel, show_error
from .ui_strings import get_ui_string
from .widgets import Tooltip, scrollbar_command

if TYPE_CHECKING:
    from src.gui.main_window import PDFHighlighterApp

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RefreshContext:
    """Identity captured on Tk before fetching one channel's release list."""

    generation: int
    refresh_id: int
    channel: str


class DevToolsWindow:
    """App-owned developer controls; workers only fetch and enqueue plain results."""

    def __init__(self, app: "PDFHighlighterApp"):
        """Keep request identities and UI state owned by this application instance."""
        self.app = app
        self.window = None
        self._generation = 0
        self._releases_refresh_id = 0
        self._context = None
        self._dispatch_queue = queue.Queue()
        self._dispatcher_id = None
        self._lifetime_cancel = threading.Event()
        self._busy = False
        self._shown = False
        self._user_sized = False
        self._auto_size = None
        self._releases_cache = {}
        self._list_channel = None
        self._load_state = "empty"
        self._text_widgets = []
        self._notes_markdown = ""

    def _text(self, key):
        """Resolve a label in the currently selected application language."""
        return get_ui_string(self.app.strings, key)

    @staticmethod
    def _capture_mode():
        """Identify screenshot runs that must avoid network activity."""
        return os.getenv("HSPH_SCREENSHOT_MODE") in ("1", "true", "True")

    def _caption(self, widget, key):
        """Register widgets that can be translated without rebuilding the window."""
        widget.configure(text=self._text(key))
        self._text_widgets.append((widget, key))
        return widget

    def open(self):
        """Build hidden, finish geometry, then show the final window once."""
        if getattr(self.app, "_closing", False):
            return
        capture = self._capture_mode()
        if capture and os.getenv("HSPH_SCREENSHOT_TARGET") != "devtools":
            return
        if self._is_open():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_set()
            return
        self._generation += 1
        self._lifetime_cancel = threading.Event()
        self._context = None
        self._text_widgets = []
        self._releases_cache = {}
        self._list_channel = self._get_channel()
        self._load_state = "empty"
        self._shown = self._user_sized = False
        self._auto_size = None
        self._notes_markdown = ""
        self.window = window = Toplevel(self.app.root)
        window.withdraw()
        window.transient(self.app.root)
        window.resizable(True, True)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Destroy>", self._on_destroy, add="+")
        window.bind("<Configure>", self._on_window_configure, add="+")
        self.app.apply_theme_to_window(window)

        viewport = ttk.Frame(window)
        viewport.grid(sticky="nsew")
        viewport.columnconfigure(0, weight=1)
        viewport.rowconfigure(0, weight=1)
        self.canvas = Canvas(viewport, width=1, height=1, highlightthickness=0, borderwidth=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar = ttk.Scrollbar(viewport, command=scrollbar_command(self.canvas.yview))
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.content = ttk.Frame(self.canvas, padding=12)
        self._content_item = self.canvas.create_window(0, 0, window=self.content, anchor="nw")
        self.content.columnconfigure(0, weight=1)
        self.content.rowconfigure(2, weight=1)
        self.canvas.bind("<Configure>", self._resize_viewport)
        self.content.bind("<Configure>", self._content_changed)
        window.bind("<MouseWheel>", self._scroll_form, add="+")
        window.bind("<FocusIn>", self._reveal_control, add="+")

        channel_frame = self._caption(ttk.LabelFrame(self.content, padding=8), "dev_update_channel")
        channel_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        channel_frame.columnconfigure(1, weight=1)
        self._caption(ttk.Label(channel_frame), "dev_channel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.channel_var = StringVar(window)
        self.channel_combo = ttk.Combobox(channel_frame, state="readonly", width=24)
        self.channel_combo.grid(row=0, column=1, sticky="ew")
        self.channel_combo.bind("<<ComboboxSelected>>", self._select_channel)
        self.sha_required = BooleanVar(window)
        self.sha_checkbox = self._caption(
            ttk.Checkbutton(channel_frame, variable=self.sha_required, command=self._set_sha_required),
            "dev_verify_sha256",
        )
        self.sha_checkbox.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        debug_frame = self._caption(ttk.LabelFrame(self.content, padding=8), "dev_debug")
        debug_frame.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        self._caption(ttk.Button(debug_frame, command=self._open_settings_file), "dev_open_settings").grid(sticky="w")

        releases_frame = self._caption(ttk.LabelFrame(self.content, padding=8), "dev_install_specific")
        releases_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
        releases_frame.columnconfigure(0, weight=1)
        releases_frame.rowconfigure(3, weight=1)
        self.releases_combo = ttk.Combobox(releases_frame, state="readonly", width=30)
        self.releases_combo.grid(row=0, column=0, sticky="ew")
        self.releases_combo.bind("<<ComboboxSelected>>", lambda _event: self._selection_changed())
        self.release_hint = ttk.Label(releases_frame, wraplength=540, justify="left")
        self.release_hint.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        actions = ttk.Frame(releases_frame)
        actions.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        actions.columnconfigure(0, weight=1)
        self.retry_button = self._caption(
            ttk.Button(actions, command=lambda: self._start_refresh_releases_async(force=True)), "btn_retry"
        )
        self.retry_button.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.install_button = self._caption(
            ttk.Button(actions, command=self._install_selected_release), "dev_btn_install"
        )
        self.install_button.grid(row=0, column=1, sticky="e", padx=(0, 8))
        self.notes_button = ttk.Button(actions, command=self._toggle_release_notes)
        self.notes_button.grid(row=0, column=2, sticky="e")
        self.notes_frame = ttk.Frame(releases_frame)
        self.notes_frame.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        self.notes_frame.columnconfigure(0, weight=1)
        self.notes_frame.rowconfigure(0, weight=1)
        self.notes_html = HtmlFrame(
            self.notes_frame,
            horizontal_scrollbar=False,
            vertical_scrollbar="auto",
            messages_enabled=False,
            shrink=False,
            fontscale=1.0,
            width=1,
            height=250,
        )
        self.notes_html.grid(sticky="nsew")
        self.release_notes_shown = False
        self.notes_frame.grid_remove()

        reset_frame = self._caption(ttk.LabelFrame(self.content, padding=8), "dev_reset_settings")
        reset_frame.grid(row=3, column=0, sticky="ew")
        self.reset_button = self._caption(ttk.Button(reset_frame, command=self._reset_settings), "dev_reset_defaults")
        self.reset_button.grid(sticky="w")
        self._sync_options()
        self.refresh_ui_strings()
        self.set_busy(getattr(self.app, "output_options_locked", False))
        if capture:
            self._populate_releases_for_screenshot()
        else:
            self._start_dispatcher()
            self._start_refresh_releases_async()
        self.app.apply_theme_to_window(window)
        self._fit_initial_geometry()
        self._shown = True
        window.deiconify()

    def _is_open(self):
        """Check whether the current window still has a live Tcl widget."""
        try:
            return bool(self.window and self.window.winfo_exists())
        except TclError:
            return False

    def close(self):
        """Invalidate workers and remove all callbacks belonging to this window."""
        window, self.window = self.window, None
        self._lifetime_cancel.set()
        self._generation += 1
        self._releases_refresh_id += 1
        self._context = None
        self._shown = False
        if self._dispatcher_id is not None:
            with suppress(TclError):
                self.app.root.after_cancel(self._dispatcher_id)
        self._dispatcher_id = None
        self._discard_queue()
        self._text_widgets = []
        if window is not None:
            with suppress(TclError):
                window.destroy()

    def _on_destroy(self, event):
        """Clean up even when a parent or external caller destroys the window."""
        if event.widget is self.window:
            self.close()

    def _discard_queue(self):
        """Drop pending messages without invoking callbacks on destroyed controls."""
        while True:
            try:
                self._dispatch_queue.get_nowait()
            except queue.Empty:
                return

    def _layout_width(self, width):
        """Wrap hints and actions using the allocated form width."""
        self.canvas.itemconfigure(self._content_item, width=max(1, width))
        self.release_hint.configure(wraplength=max(80, width - 48))
        # Keep translated action buttons together, wrapping when the viewport is narrow.
        total = sum(
            button.winfo_reqwidth() + 8 for button in (self.retry_button, self.install_button, self.notes_button)
        )
        self.notes_button.grid(
            row=1 if total > width - 48 else 0,
            column=0 if total > width - 48 else 2,
            columnspan=3 if total > width - 48 else 1,
            sticky="e",
            pady=(4, 0),
        )

    def _resize_viewport(self, event):
        """Keep the embedded form aligned with the scrollable viewport."""
        self._layout_width(event.width)
        self._content_changed()

    def _content_changed(self, _event=None):
        """Fill spare height while preserving the full scrollable content extent."""
        height = max(self.content.winfo_reqheight(), self.canvas.winfo_height())
        self.canvas.itemconfigure(self._content_item, height=height)
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _scroll_form(self, event):
        """Leave wheel events inside the HTML document to its own scrollbar."""
        if event.widget in (self.scrollbar, self.channel_combo, self.releases_combo):
            return
        widget = event.widget
        while widget is not None and widget is not self.window:
            if widget.__class__.__module__.startswith("tkinterweb"):
                return
            widget = getattr(widget, "master", None)
        if event.delta:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _reveal_control(self, event):
        """Scroll keyboard-focused controls into the visible part of the form."""
        widget = event.widget
        if not str(widget).startswith(str(self.content) + "."):
            return
        top = widget.winfo_rooty() - self.content.winfo_rooty()
        bottom = top + widget.winfo_height()
        visible = self.canvas.canvasy(0)
        if top < visible or bottom > visible + self.canvas.winfo_height():
            target = max(0, top - 8) if top < visible else bottom + 8 - self.canvas.winfo_height()
            self.canvas.yview_moveto(target / max(1, self.content.winfo_height()))

    def _fit_initial_geometry(self):
        """Measure hidden content and place its complete frame beside the parent."""
        self._work = monitor_work_area(self.app.root)
        self.window.update_idletasks()
        self._padding = frame_padding(self.window)
        font = getattr(self.app, "ui_font", None)
        minimum = max(320, font.measure("M" * 18) + 48) if font else 320
        max_width = max(1, self._work.width - self._padding[0])
        max_height = max(1, self._work.height - self._padding[1])
        self.window.minsize(min(minimum, max_width), min(180, max_height))
        width = min(max_width, max(620, minimum))
        self._layout_width(max(1, width - self.scrollbar.winfo_reqwidth()))
        self.window.update_idletasks()
        height = min(max_height, max(self.window.minsize()[1], self.content.winfo_reqheight()))
        bounds = beside_parent(
            window_bounds(self.app.root), (width + self._padding[0], height + self._padding[1]), self._work
        )
        self._auto_size = (width, height)
        apply_bounds(self.window, bounds, self._padding)

    def _on_window_configure(self, event):
        """Remember manual resizing so later toggles do not replace user geometry."""
        if event.widget is self.window and self._shown and self._auto_size is not None:
            if (event.width, event.height) != self._auto_size:
                self._user_sized = True

    def _toggle_release_notes(self):
        """Fit automatic heights to notes while retaining manually resized windows."""
        self.release_notes_shown = not self.release_notes_shown
        if self.release_notes_shown:
            self.notes_frame.grid()
        else:
            self.notes_frame.grid_remove()
        self._render_notes_button()
        if not self._user_sized:
            self._work = monitor_work_area(self.window)
            self.window.update_idletasks()
            current = window_bounds(self.window)
            height = min(
                self._work.height,
                max(self.window.minsize()[1] + self._padding[1], self.content.winfo_reqheight() + self._padding[1]),
            )
            bounds = clamp_bounds(Bounds(current.left, current.top, current.right, current.top + height), self._work)
            self._auto_size = (bounds.width - self._padding[0], bounds.height - self._padding[1])
            apply_bounds(self.window, bounds, self._padding)
        self._content_changed()

    def _render_notes_button(self):
        """Translate the accessible notes toggle and its expanded indicator."""
        arrow = "\u25bc" if self.release_notes_shown else "\u25b6"
        self.notes_button.configure(text=f"{arrow} {self._text('dev_release_notes')}")

    def _sync_options(self):
        """Read committed settings without firing selection or persistence handlers."""
        settings = self.app.app_settings.snapshot()
        self.channel_var.set(settings.get("update_channel", "stable"))
        self.sha_required.set(settings.get("verify_sha", "True") == "True")
        labels = {key: self._text(label) for key, label in (("stable", "dev_stable"), ("rc", "dev_rc"))}
        self._key_by_label = {label: key for key, label in labels.items()}
        self.channel_combo.configure(values=list(labels.values()))
        self.channel_combo.set(labels[self.channel_var.get()])

    def refresh_ui_strings(self):
        """Translate existing widgets while keeping geometry, notes and selection."""
        if not self._is_open():
            return
        self.window.title(self._text("dev_tools"))
        for widget, key in self._text_widgets:
            widget.configure(text=self._text(key))
        self._sync_options()
        Tooltip(self.sha_checkbox, self._text("dev_sha256_info"))
        self._render_notes_button()
        if self._list_channel != self._get_channel():
            self._clear_releases()
            self._list_channel = self._get_channel()
            if self._capture_mode():
                self._context = None
                self._populate_releases_for_screenshot()
            else:
                self._start_refresh_releases_async()
        self._render_release_state()
        self.refresh_theme()

    def refresh_theme(self):
        """Restyle the live notes document instead of keeping its previous colors."""
        if not self._is_open():
            return
        fraction = self.notes_html.html.yview()[0]
        colors = getattr(self.app, "_current_theme_colors", None)
        if colors is not None:
            ttk.Style(self.window).configure("DevNotes.TFrame", background=colors.field_background)
            self.notes_html.configure(style="DevNotes.TFrame")
        self._set_notes_text(self._notes_markdown)
        self._render_release_state()
        if self._shown:
            self.window.update_idletasks()
            self._layout_width(self.canvas.winfo_width())
            self.window.update_idletasks()
        self.notes_html.yview_moveto(fraction)

    @staticmethod
    def md_to_html(md_text):
        """Render release markdown using the existing supported formatting features."""
        return markdown2.markdown(
            md_text,
            extras=[
                "fenced-code-blocks",
                "tables",
                "strike",
                "task_list",
                "toc",
                "header-ids",
                "break-on-newline",
                "code-friendly",
                "smarty-pants",
            ],
        )

    def _themed_html(self, body_html):
        """Build the document style from current colors and the named UI font size."""
        colors = getattr(self.app, "_current_theme_colors", None)
        if colors is None:
            return body_html
        font = getattr(self.app, "ui_font", None)
        size = abs(int(font.cget("size"))) if font else 10
        return f"""<html><head><style>
        body {{background:{colors.field_background};color:{colors.foreground};font-family:Arial,sans-serif;
               font-size:{max(1, size)}pt;margin:10px;}}
        a {{color:{colors.select_background};}}
        code,pre {{background:{colors.background};color:{colors.foreground};border:1px solid {colors.border};}}
        </style></head><body>{body_html}</body></html>"""

    def _set_notes_text(self, text):
        """Retain source markdown for live restyling and escape any rendering fallback."""
        self._notes_markdown = text or ""
        try:
            body = self.md_to_html(self._notes_markdown)
        except Exception:
            logger.exception("Could not render release notes")
            body = "<pre>" + html.escape(self._notes_markdown) + "</pre>"
        self.notes_html.load_html(self._themed_html(body))

    def _update_release_notes(self):
        """Display the notes belonging to the selected release record."""
        record = self._releases_cache.get(self.releases_combo.get(), {})
        self._set_notes_text(record.get("body") or record.get("notes") or "")

    def _selection_changed(self):
        """Update notes and install eligibility together after browsing releases."""
        self._update_release_notes()
        self._render_release_state()

    def _get_channel(self):
        """Read the committed release channel on the Tk thread."""
        return self.app.app_settings.settings.get("update_channel", "stable")

    def app_update_current_version(self):
        """Parse the currently configured application version."""
        return Version.from_str(self.app.app_settings.settings["version"])

    def _controls_locked(self):
        """Include the downloader reservation before its GUI state is established."""
        return bool(
            self._busy
            or getattr(self.app, "output_options_locked", False)
            or getattr(self.app.update_checker, "_active_download", False)
        )

    def set_busy(self, busy):
        """Guard update-affecting actions while leaving browsing available."""
        self._busy = busy
        if self._is_open():
            self._render_release_state()

    def _select_channel(self, _event=None):
        """Map a localized selection back to the persistent channel key."""
        self.channel_var.set(self._key_by_label.get(self.channel_combo.get(), self._get_channel()))
        self._on_channel_changed()

    def _on_channel_changed(self):
        """Clear old-channel data before starting replacement checks."""
        if self._controls_locked():
            self._sync_options()
            return
        channel = self.channel_var.get()
        if channel == self._get_channel():
            return
        self.app.app_settings.update_settings(
            {"update_channel": channel, "newest_version_available": "0.0.0", "ask_for_update": "True"}
        )
        self.app.update_checker.channel_changed()
        current = self.app_update_current_version()
        self.app.update_version_labels_text(current, current)
        self.app.update_version_labels()
        self._clear_releases()
        self._list_channel = channel
        self._sync_options()
        if self._capture_mode():
            self._context = None
            self._render_release_state()
            return
        self._start_refresh_releases_async()

        def check():
            try:
                self.app.update_checker.check_for_app_updates(current_version=current, force_check=True, quiet=True)
            except Exception:
                logger.exception("Could not check the newly selected update channel")

        threading.Thread(target=check, daemon=True).start()

    def _set_sha_required(self):
        """Persist checksum policy only when no owned operation has locked it."""
        if self._controls_locked():
            self._sync_options()
            return
        self.app.app_settings.update_setting("verify_sha", "True" if self.sha_required.get() else "False")
        self._render_release_state()

    def _clear_releases(self):
        """Remove stale choices and notes immediately on a channel change."""
        self._releases_cache = {}
        self.releases_combo.configure(values=[])
        self.releases_combo.set("")
        self._set_notes_text("")
        self._load_state = "empty"

    def _apply_releases(self, releases):
        """Keep the selected tag on same-channel refreshes when it still exists."""
        selected = self.releases_combo.get()
        self._releases_cache = {
            r["tag"]: r for r in releases if isinstance(r, dict) and isinstance(r.get("tag"), str) and r.get("exe_url")
        }
        tags = list(self._releases_cache)
        self.releases_combo.configure(values=tags)
        self.releases_combo.set(selected if selected in tags else tags[0] if tags else "")
        self._load_state = "ready" if tags else "empty"
        self._selection_changed()

    def _render_release_state(self):
        """Derive control states and useful hints from data readiness and ownership."""
        locked = self._controls_locked()
        self.channel_combo.configure(state="disabled" if locked else "readonly")
        self.sha_checkbox.configure(state="disabled" if locked else "normal")
        self.reset_button.configure(state="disabled" if locked else "normal")
        self.releases_combo.configure(state="readonly" if self._releases_cache else "disabled")
        record = self._releases_cache.get(self.releases_combo.get(), {})
        missing_checksum = bool(record and self.sha_required.get() and not record.get("sha_url"))
        eligible = bool(record.get("exe_url") and not missing_checksum)
        self.install_button.configure(state="normal" if eligible and not locked else "disabled")
        key = {
            "empty": "dev_versions_empty",
            "loading": "dev_versions_loading",
            "failed": "dev_versions_failed",
            "cached_failed": "dev_versions_cached",
        }.get(self._load_state)
        hints = [self._text(key)] if key else []
        if missing_checksum:
            hints.append(self._text("dev_checksum_missing"))
        self.release_hint.configure(text="\n".join(hints))
        self.retry_button.configure(state="disabled" if self._load_state == "loading" else "normal")

    def _valid_context(self, context):
        """Accept only the current window, request and committed channel."""
        return bool(
            self._is_open()
            and not getattr(self.app, "_closing", False)
            and self._context == context
            and context.channel == self._get_channel()
        )

    def _start_refresh_releases_async(self, force=False):
        """Capture context on Tk and apply cached/network results through one gate."""
        if not self._is_open() or getattr(self.app, "_closing", False):
            return
        self._releases_refresh_id += 1
        channel = self._get_channel()
        if self._list_channel != channel:
            self._clear_releases()
        self._list_channel = channel
        context = _RefreshContext(self._generation, self._releases_refresh_id, channel)
        self._context = context
        try:
            fetched_at, cached_channel, cached = load_releases_cache()
        except Exception:
            logger.exception("Could not read the releases cache")
            fetched_at, cached_channel, cached = None, None, None
        valid_cache = cached_channel == channel and isinstance(cached, list)
        if valid_cache:
            self._complete_refresh(context, cached, failed=False, fetched_at=None)
        ttl = int(self.app.app_settings.settings.get("releases_cache_ttl_seconds", 600))
        if not self._should_fetch_releases(force, fetched_at if valid_cache else None, cached_channel, channel, ttl):
            return
        self._load_state = "ready" if self._releases_cache else "loading"
        self._render_release_state()
        self._start_dispatcher()
        checker, outbox, cancelled = self.app.update_checker, self._dispatch_queue, self._lifetime_cancel

        def fetch():
            try:
                releases = checker.list_releases(channel=context.channel)
                result = (context, releases, False, datetime.datetime.now())
            except Exception:
                logger.exception("Could not load release versions")
                result = (context, None, True, None)
            if not cancelled.is_set():
                outbox.put(result)

        threading.Thread(target=fetch, daemon=True).start()

    @staticmethod
    def _should_fetch_releases(force, fetched_at, cached_channel, current_channel, ttl_seconds):
        """Retain the existing cache channel and expiry rules."""
        if force or not fetched_at or cached_channel != current_channel:
            return True
        try:
            return (datetime.datetime.now() - fetched_at).total_seconds() > ttl_seconds
        except TypeError, ValueError:
            return True

    def _complete_refresh(self, context, releases, failed, fetched_at):
        """Reject stale completions before they can publish errors, UI or cache data."""
        if not self._valid_context(context):
            return
        if failed:
            self._load_state = "cached_failed" if self._releases_cache else "failed"
            self._render_release_state()
            return
        try:
            self._apply_releases(releases)
        except Exception:
            logger.exception("Invalid releases response")
            self._load_state = "failed"
            self._render_release_state()
            return
        if fetched_at is not None:
            try:
                save_releases_cache(releases=releases, channel=context.channel, fetched_at=fetched_at)
            except Exception:
                logger.exception("Could not save the releases cache")

    def _start_dispatcher(self):
        """Keep a single main-thread queue poll active for the open window."""
        if self._dispatcher_id is None and self._is_open():
            self._dispatcher_id = self.app.root.after(50, self._poll_dispatcher)

    def _poll_dispatcher(self):
        """Deliver queued messages and stop polling once the window closes."""
        self._dispatcher_id = None
        self._drain_queue()
        if self._is_open() and not getattr(self.app, "_closing", False):
            self._start_dispatcher()

    def _drain_queue(self):
        """Apply worker messages only through the shared context validation gate."""
        while True:
            try:
                result = self._dispatch_queue.get_nowait()
            except queue.Empty:
                return
            self._complete_refresh(*result)

    def _install_candidate(self):
        """Capture the selected tag, asset URLs and checksum requirement."""
        tag = self.releases_combo.get()
        record = self._releases_cache.get(tag, {})
        verify = self.sha_required.get()
        if not record.get("exe_url") or (verify and not record.get("sha_url")):
            return None
        return tag, record["exe_url"], record.get("sha_url") if verify else None, verify

    def _install_selected_release(self):
        """Revalidate a confirmed installation before delegating to the updater."""
        if self._controls_locked() or not self._is_open():
            return
        candidate = self._install_candidate()
        if candidate is None:
            self._render_release_state()
            return
        context, generation, channel = self._context, self._generation, self._get_channel()
        if not ask_ok_cancel(
            self.app, self._text("dev_install_specific"), self._text("dev_confirm_install").format(candidate[0])
        ):
            return
        if (
            self._controls_locked()
            or not self._is_open()
            or generation != self._generation
            or context != self._context
            or channel != self._get_channel()
            or candidate != self._install_candidate()
        ):
            return
        checker, exe_url, sha_url = self.app.update_checker, candidate[1], candidate[2]
        threading.Thread(target=lambda: checker.download_and_run_installer(exe_url, sha_url), daemon=True).start()

    def _reset_settings(self):
        """Recheck ownership and window lifetime after the modal confirmation."""
        if self._controls_locked():
            return
        generation = self._generation
        if not ask_ok_cancel(self.app, self._text("dev_reset_settings"), self._text("dev_confirm_reset")):
            return
        if self._controls_locked() or generation != self._generation:
            return
        self.app.reset_settings()

    def _open_settings_file(self):
        """Offer a useful file path on failure while keeping diagnostics in logs."""
        try:
            path = str(self.app.paths.settings_file)
            if not webbrowser.open(path):
                raise OSError("No application accepted the settings file")
        except Exception:
            logger.exception("Could not open the settings file")
            show_error(
                self.app,
                self._text("error"),
                self._text("dev_settings_open_failed").format(self.app.paths.settings_file),
            )

    def _populate_releases_for_screenshot(self):
        """Provide deterministic release data without fetching or installing anything."""
        _, channel, cached = load_releases_cache()
        if cached and channel == self._get_channel():
            self._apply_releases(cached)
        else:
            tag = str(self.app.app_settings.settings.get("version", "1.0.0"))
            self._apply_releases(
                [
                    {
                        "tag": tag,
                        "exe_url": "https://example.invalid/installer.exe",
                        "sha_url": "https://example.invalid/installer.exe.sha256",
                        "body": f"Release notes for {tag}.",
                    }
                ]
            )
