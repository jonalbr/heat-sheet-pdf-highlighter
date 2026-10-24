"""Transactional name filtering, highlight colors and a live PDF excerpt."""

import csv
from io import BytesIO
import logging
from pathlib import Path
from tkinter import Button, StringVar, TclError, Text, Toplevel, colorchooser, filedialog, ttk
from tkinter.font import Font

from PIL import Image, ImageTk

from ..core.marking_preview import PREVIEW_HEIGHT, PREVIEW_WIDTH, ExampleText, PreviewRequest
from ..core.pdf_processor import parse_names
from ..models import MarkingMode
from ..utils.theme import get_theme_colors
from .marking_preview import MarkingPreview
from .message_dialog import show_error
from .ui_strings import get_ui_string
from .widgets import Tooltip, lock_controls

PALETTE = (
    ("flt_yellow", "#FFFFA6"),
    ("flt_turquoise", "#C4FAF8"),
    ("flt_green", "#D5F5B0"),
    ("flt_orange", "#FFD6A6"),
    ("flt_pink", "#FFD1E3"),
    ("flt_violet", "#E0D1FF"),
)


class FilterDialog:
    """Keep edits local until Apply, including across language/theme changes."""

    def __init__(self, app_instance):
        self.app = app_instance
        self.window = None
        self._preview = None
        self._photo = None
        self._external_traces = []
        self._busy = False
        self._last_preview = None
        self._request_key = None

    def set_busy(self, busy):
        """Keep the draft while temporarily disabling all edit/apply controls."""
        self._busy = busy
        if self.window is not None and self.window.winfo_exists():
            lock_controls(self.window, busy, (self.cancel_button,))
            if not busy:
                self._update_controls()

    def _text(self, key):
        return get_ui_string(self.app.strings, key)

    def open(self, draft=None):
        """Open the dialog, optionally restoring a draft after a language change."""
        if self.window is not None and self.window.winfo_exists():
            self.window.lift()
            return
        draft = draft or {
            "names": self.app.names_var.get(),
            "marking_mode": self.app.marking_mode_var.get(),
            "highlight_color_default": self.app.highlight_color_default_var.get(),
            "highlight_color_names": self.app.highlight_color_names_var.get(),
        }
        self.window = window = Toplevel(self.app.root)
        window.withdraw()
        window.title(self._text("btn_filter"))
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Destroy>", self._on_destroy, add="+")
        self.mode = StringVar(window, value=draft["marking_mode"])
        self.colors = {
            key: StringVar(window, value=draft[key])
            for key in (
                "highlight_color_default",
                "highlight_color_names",
            )
        }
        content = ttk.Frame(window, padding=12)
        content.grid(sticky="nsew")
        content.columnconfigure(1, weight=1)
        self.mode_buttons = []
        for row, (mode, key) in enumerate(
            (
                (MarkingMode.ALL_MATCHES, "flt_all"),
                (MarkingMode.DISTINGUISH_NAMES, "flt_distinguish"),
                (MarkingMode.NAMED_MATCHES, "flt_named"),
            )
        ):
            button = ttk.Radiobutton(content, text=self._text(key), variable=self.mode, value=mode.value)
            button.grid(row=row, column=0, columnspan=2, sticky="w", pady=3)
            self.mode_buttons.append(button)
        self.names_label = ttk.Label(content, text=self._text("flt_names"))
        self.names_label.grid(row=3, column=0, sticky="nw", pady=(12, 0), padx=(0, 12))
        self.names_entry = Text(content, height=4, width=48, wrap="word")
        self.names_entry.insert("1.0", draft["names"])
        self.names_entry.grid(row=3, column=1, sticky="ew", pady=(12, 0))
        self.names_entry.bind("<<Modified>>", self._names_changed)
        self.names_entry.bind("<Return>", self._insert_comma)
        self.names_entry.edit_modified(False)
        actions = ttk.Frame(content)
        actions.grid(row=4, column=1, sticky="w", pady=6)
        self.name_buttons = [
            ttk.Button(actions, text=self._text("btn_clear"), command=self._clear_names),
            ttk.Button(actions, text=self._text("btn_import"), command=self._import_names),
        ]
        for button in self.name_buttons:
            button.pack(side="left", padx=(0, 8))
        self.color_widgets = {}
        for row, key, label_key in (
            (5, "highlight_color_default", "flt_color_default"),
            (6, "highlight_color_names", "flt_color_names"),
        ):
            label = ttk.Label(content, text=self._text(label_key))
            label.grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
            frame = ttk.Frame(content)
            frame.grid(row=row, column=1, sticky="w", pady=6)
            current = Button(frame, width=3, relief="flat", command=lambda key=key: self._choose_color(key))
            current.pack(side="left", padx=(0, 10))
            widgets = [(current, None)]
            for color_key, color in PALETTE:
                button = Button(
                    frame, width=2, relief="flat", command=lambda key=key, color=color: self.colors[key].set(color)
                )
                button.pack(side="left", padx=2)
                widgets.append((button, color))
                Tooltip(button, self._text(color_key))
            custom = ttk.Button(frame, text=self._text("flt_custom"), command=lambda key=key: self._choose_color(key))
            custom.pack(side="left", padx=(8, 0))
            self.color_widgets[key] = (label, widgets, custom)
            self.colors[key].trace_add("write", self._changed)
        preview_frame = ttk.LabelFrame(content, text=self._text("flt_preview"), padding=6)
        preview_frame.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(12, 6))
        self.preview_label = ttk.Label(preview_frame, text=self._text("flt_example"))
        self.preview_label.pack(anchor="w")
        self.preview_viewport = ttk.Frame(preview_frame, width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT)
        self.preview_viewport.pack(pady=(4, 0))
        self.preview_viewport.pack_propagate(False)
        self.preview_image = ttk.Label(self.preview_viewport, anchor="center")
        self.preview_image.pack(fill="both", expand=True)
        note_font = Font(root=window, font=ttk.Style(window).lookup("TLabel", "font") or "TkDefaultFont")
        self.preview_note_space = ttk.Frame(preview_frame, width=PREVIEW_WIDTH, height=note_font.metrics("linespace") * 2)
        self.preview_note_space.pack(pady=(6, 0))
        self.preview_note_space.pack_propagate(False)
        self.preview_note = ttk.Label(self.preview_note_space, text="", wraplength=PREVIEW_WIDTH, anchor="nw")
        self.preview_note.pack(fill="both", expand=True)
        footer = ttk.Frame(content)
        footer.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(footer, text=self._text("btn_apply"), command=self.apply_changes).pack(side="left")
        self.cancel_button = ttk.Button(footer, text=self._text("btn_cancel"), command=self.close)
        self.cancel_button.pack(side="right")
        self._preview = MarkingPreview(window, self._show_preview, self._preview_failed)
        self.mode.trace_add("write", self._changed)
        for variable in (self.app.search_phrase_var, self.app.relevant_lines_var):
            self._external_traces.append((variable, variable.trace_add("write", self._changed)))
        self.app.apply_theme_to_window(window)
        self.set_busy(getattr(self.app, "output_options_locked", False))
        self._changed(immediate=True)

    def _draft(self):
        return {
            "names": self.names_entry.get("1.0", "end-1c"),
            "marking_mode": self.mode.get(),
            **{key: variable.get() for key, variable in self.colors.items()},
        }

    def _update_controls(self):
        """Show inactive controls without losing their configured values."""
        mode = MarkingMode(self.mode.get())
        if self._busy:
            return mode
        names_active = mode != MarkingMode.ALL_MATCHES
        theme = get_theme_colors(getattr(self.app, "_current_effective_theme", "light"))
        self.names_label.configure(state="normal" if names_active else "disabled")
        self.names_entry.configure(
            state="normal" if names_active else "disabled",
            foreground=theme.foreground if names_active else theme.muted_foreground,
        )
        for button in self.name_buttons:
            button.configure(state="normal" if names_active else "disabled")
        for key, (label, widgets, custom) in self.color_widgets.items():
            active = names_active if key == "highlight_color_names" else mode != MarkingMode.NAMED_MATCHES
            state = "normal" if active else "disabled"
            label.configure(state=state)
            custom.configure(state=state)
            Tooltip(label, "" if active else self._text("flt_unused"))
            Tooltip(custom, "" if active else self._text("flt_unused"))
            for button, preset in widgets:
                color = preset or self.colors[key].get()
                if preset and not active:
                    shade = round(sum(int(color[i : i + 2], 16) for i in (1, 3, 5)) / 3)
                    color = "#" + f"{shade:02X}" * 3
                button._hsph_swatch_color = color
                button._hsph_swatch_selected = preset == self.colors[key].get()
                button.configure(
                    state=state,
                    background=color,
                    activebackground=color,
                    relief="sunken" if button._hsph_swatch_selected else "flat",
                )
                color_name = next((self._text(name) for name, value in PALETTE if value == preset), color)
                Tooltip(button, color_name if active else self._text("flt_unused"))
        return mode

    def refresh_theme(self):
        """Restore disabled colors after the app themes classic Tk widgets."""
        if self.window is not None:
            self._update_controls()

    def _changed(self, *_, immediate=False):
        if self.window is None:
            return
        mode = self._update_controls()
        self.preview_note.configure(text="")
        request = PreviewRequest(
            getattr(self.app, "input_file_full_path", None),
            self.app.search_phrase_var.get(),
            bool(self.app.relevant_lines_var.get()),
            parse_names(self.names_entry.get("1.0", "end-1c")),
            mode,
            ExampleText(
                heading=self._text("flt_example_heat").format(1, 3, "10:30"),
                lane=self._text("flt_example_lane"),
                swimmer=self._text("flt_example_swimmer"),
                year=self._text("flt_example_year"),
                club=self._text("flt_example_club"),
                entry_time=self._text("flt_example_entry_time"),
                lane_format=self._text("flt_example_lane_format"),
                time_format=self._text("flt_example_time_format"),
            ),
            self.colors["highlight_color_default"].get(),
            self.colors["highlight_color_names"].get(),
        )
        fingerprint = None
        if request.source_path:
            try:
                stat = Path(request.source_path).stat()
                fingerprint = (stat.st_mtime_ns, stat.st_size)
            except OSError:
                pass
        self._request_key = (request, fingerprint)
        # Always invalidate obsolete in-flight results, even when displaying a
        # cached image immediately after reopening the dialog.
        self._preview.request(request, immediate=immediate)
        if self._last_preview is not None and self._last_preview[0] == self._request_key:
            self._show_preview(self._last_preview[1])

    def _names_changed(self, *_):
        if self.names_entry.edit_modified():
            self.names_entry.edit_modified(False)
            self._changed()

    def _insert_comma(self, *_):
        if not self.names_entry.get("1.0", "end-1c").rstrip().endswith(","):
            self.names_entry.insert("end", ", ")
        return "break"

    def _clear_names(self):
        self.names_entry.delete("1.0", "end")

    def _import_names(self):
        filename = filedialog.askopenfilename(
            parent=self.window,
            filetypes=[
                (self._text("file_filter_csv"), "*.csv;*.txt"),
                (self._text("file_filter_all"), "*.*"),
            ],
        )
        if filename:
            try:
                with open(filename, encoding="utf-8-sig") as source:
                    names = (
                        next(csv.reader(source), [])
                        if filename.lower().endswith(".csv")
                        else parse_names(source.read())
                    )
                self.names_entry.delete("1.0", "end")
                self.names_entry.insert("1.0", ", ".join(names))
            except OSError, UnicodeError, csv.Error:
                logging.getLogger(__name__).exception("Cannot import names")

    def _choose_color(self, key):
        _, color = colorchooser.askcolor(color=self.colors[key].get(), parent=self.window)
        if color and self.window is not None:
            self.colors[key].set(color.upper())

    def _show_preview(self, result):
        if self.window is None:
            return
        with Image.open(BytesIO(result.png)) as image:
            self._photo = ImageTk.PhotoImage(image, master=self.window)
        self.preview_image.configure(image=self._photo)
        self.preview_label.configure(
            text=(
                self._text("flt_pdf_excerpt").format(result.page_number)
                if result.page_number is not None
                else self._text("flt_example")
            )
        )
        self.preview_note.configure(text=self._text("flt_no_names_found") if result.no_named_matches else "")
        self._last_preview = (self._request_key, result)
        self._reveal()

    def _reveal(self):
        """Map the completely laid-out dialog after its first preview or error."""
        if self.window is not None and self.window.state() == "withdrawn":
            self.window.update_idletasks()
            self.window.deiconify()
            self.window.grab_set()
            self.window.focus_set()

    def _preview_failed(self):
        """Keep settings usable if the preview process could not return an image."""
        if self.window is not None:
            self.preview_note.configure(text=self._text("flt_preview_unavailable"))
            self._reveal()

    def apply_changes(self):
        """Validate the complete draft before publishing any setting."""
        if self._busy or getattr(self.app, "output_options_locked", False):
            return
        draft = self._draft()
        if draft["marking_mode"] != MarkingMode.ALL_MATCHES and not parse_names(draft["names"]):
            show_error(self.app, self._text("error"), self._text("flt_names_required"))
            return
        # Publish settings together so variable traces never persist a partial draft.
        self.app.app_settings.settings.update(draft)
        self.app.app_settings.save_settings()
        for key, value in draft.items():
            getattr(self.app, f"{key}_var").set(value)
        self.close()

    def _on_destroy(self, event):
        if event.widget is self.window:
            self._cleanup()
            self.window = None

    def _cleanup(self):
        if self._preview is not None:
            self._preview.close()
            worker = getattr(self._preview, "cleanup_worker", None)
            if worker is not None:
                if not hasattr(self.app, "_preview_cleanups"):
                    self.app._preview_cleanups = []
                self.app._preview_cleanups = [thread for thread in self.app._preview_cleanups if thread.is_alive()]
                self.app._preview_cleanups.append(worker)
            self._preview = None
        for variable, trace in self._external_traces:
            variable.trace_remove("write", trace)
        self._external_traces.clear()
        self._photo = None

    def close(self):
        """Discard draft values and release the owned preview worker."""
        self._cleanup()
        if self.window is not None:
            try:
                self.window.destroy()
            except TclError:
                pass
            self.window = None

    def refresh_ui_strings(self):
        """Rebuild translated controls while preserving uncommitted values."""
        if self.window is not None and self.window.winfo_exists():
            draft = self._draft()
            self.close()
            self.open(draft)
