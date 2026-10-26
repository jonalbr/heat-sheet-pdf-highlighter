"""Transactional name filtering, highlight colors and a live PDF excerpt."""

import csv
from io import BytesIO
import logging
from tkinter import Button, StringVar, TclError, Text, Toplevel, colorchooser, filedialog, ttk

from PIL import Image, ImageTk

from ..core.marking_preview import EXAMPLE_PAGE_SIZE, PREVIEW_HEIGHT, PREVIEW_WIDTH, ExampleText, PreviewRequest
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
        self._photos = []

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
        window.columnconfigure(0, weight=1)
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
            self.colors[key].trace_add("write", lambda *_: self._changed(immediate=True))
        preview_frame = ttk.LabelFrame(content, text=self._text("flt_preview"), padding=6)
        preview_frame.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(12, 6))
        self.preview_label = ttk.Label(preview_frame, text="")
        self.preview_label.pack(anchor="w")
        # The synthetic example has a known aspect ratio. Real PDF excerpts
        # retain a fixed viewport while asynchronous results change.
        self._preview_height = PREVIEW_HEIGHT if getattr(self.app, "input_file_full_path", None) else min(
            PREVIEW_HEIGHT, round(PREVIEW_WIDTH * EXAMPLE_PAGE_SIZE[1] / EXAMPLE_PAGE_SIZE[0]),
        )
        self.preview_viewport = ttk.Frame(preview_frame, width=PREVIEW_WIDTH, height=self._preview_height)
        self.preview_viewport.pack(pady=(4, 0))
        self.preview_viewport.pack_propagate(False)
        self.preview_panels = []
        for _ in range(2):
            panel = ttk.Frame(self.preview_viewport)
            heading = ttk.Label(panel, anchor="w")
            image = ttk.Label(panel, anchor="center")
            image.pack(fill="both", expand=True)
            self.preview_panels.append((panel, heading, image))
        self.preview_panels[0][0].place(x=0, y=0, relwidth=1, height=self._preview_height)
        self.preview_image = self.preview_panels[0][2]
        self.preview_note_space = ttk.Frame(preview_frame, width=PREVIEW_WIDTH)
        self.preview_note = ttk.Label(self.preview_note_space, text="", wraplength=PREVIEW_WIDTH, anchor="nw")
        self.preview_note.pack(fill="both", expand=True)
        footer = ttk.Frame(content)
        footer.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        footer.columnconfigure(0, weight=1)
        self.apply_button = ttk.Button(footer, text=self._text("btn_apply"), command=self.apply_changes)
        self.apply_button.grid(row=0, column=1, sticky="e", padx=(0, 8))
        self.cancel_button = ttk.Button(footer, text=self._text("btn_cancel"), command=self.close)
        self.cancel_button.grid(row=0, column=2, sticky="e")
        self._preview = self._shared_preview()
        self._preview.subscribe(self._show_preview, self._preview_failed)
        self.mode.trace_add("write", lambda *_: self._changed(immediate=True))
        for variable, callback in (
            (self.app.search_phrase_var, lambda *_: self._changed(delay_ms=300)),
            (self.app.relevant_lines_var, lambda *_: self._changed(immediate=True)),
        ):
            self._external_traces.append((variable, variable.trace_add("write", callback)))
        self.app.apply_theme_to_window(window)
        self.set_busy(getattr(self.app, "output_options_locked", False))
        self._reveal()
        self._changed(immediate=True)

    def _shared_preview(self):
        """Borrow the app-owned worker; closing this dialog only detaches it."""
        preview = getattr(self.app, "marking_preview", None)
        if preview is None or preview._closed:
            if preview is not None and preview.cleanup_worker is not None:
                self.app._preview_cleanups = getattr(self.app, "_preview_cleanups", []) + [preview.cleanup_worker]
            preview = self.app.marking_preview = MarkingPreview(self.app.root)
            self.app.root.after_idle(preview.warm)
        return preview

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

    def capture_request(self):
        """Capture options for either an open draft or closed-dialog prefetch."""
        draft = self._draft() if self.window is not None else {
            "names": self.app.names_var.get(),
            "marking_mode": self.app.marking_mode_var.get(),
            "highlight_color_default": self.app.highlight_color_default_var.get(),
            "highlight_color_names": self.app.highlight_color_names_var.get(),
        }
        return PreviewRequest(
            getattr(self.app, "input_file_full_path", None), self.app.search_phrase_var.get(),
            bool(self.app.relevant_lines_var.get()), parse_names(draft["names"]), MarkingMode(draft["marking_mode"]),
            ExampleText(
                heading=self._text("flt_example_heat").format(1, 3, "10:30"),
                lane=self._text("flt_example_lane"), swimmer=self._text("flt_example_swimmer"),
                year=self._text("flt_example_year"), club=self._text("flt_example_club"),
                entry_time=self._text("flt_example_entry_time"), lane_format=self._text("flt_example_lane_format"),
                time_format=self._text("flt_example_time_format"),
            ), draft["highlight_color_default"], draft["highlight_color_names"],
        )

    def _changed(self, *_, immediate=False, delay_ms=200):
        if self.window is None:
            return
        self._update_controls()
        self.preview_note.configure(text="")
        if self._preview._closed:
            self._preview = self._shared_preview()
            self._preview.subscribe(self._show_preview, self._preview_failed)
        self._preview.request(self.capture_request(), immediate=immediate, delay_ms=delay_ms)

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
        self._photos = []
        snippets = result.snippets or (result,)
        split = len(snippets) == 2
        for i, (panel, heading, label) in enumerate(self.preview_panels):
            if i >= len(snippets):
                panel.place_forget()
                continue
            snippet = snippets[i]
            with Image.open(BytesIO(snippet.png)) as image:
                photo = ImageTk.PhotoImage(image, master=self.window)
            self._photos.append(photo)
            label.configure(image=photo, text="")
            if split:
                key = "flt_preview_named_page" if snippet.role == "named" else "flt_preview_other_page"
                heading.configure(text=self._text(key).format(snippet.page_number))
                heading.pack(side="top", anchor="w", before=label)
            else:
                heading.pack_forget()
            panel.place(x=0, y=i * self._preview_height // len(snippets), relwidth=1,
                        height=self._preview_height // len(snippets))
        self._photo = self._photos[0]
        self.preview_label.configure(text=self._text("flt_preview_excerpts") if split else (
            self._text("flt_pdf_excerpt").format(result.page_number) if result.page_number is not None else self._text(
                "flt_example_select_pdf" if not getattr(self.app, "input_file_full_path", None) else "flt_example")))
        state_keys = {"single_category": "flt_preview_single",
                      "no_text": "flt_preview_no_text", "no_matches": "flt_preview_no_matches",
                      "unavailable": "flt_preview_unavailable"}
        key = "flt_no_names_found" if result.no_named_matches else state_keys.get(result.state)
        self._set_preview_note(self._text(key) if key else "")

    def _set_preview_note(self, text):
        """Allocate message space only while there is a message to display."""
        self.preview_note.configure(text=text)
        if text:
            self.preview_note_space.pack(pady=(6, 0))
        else:
            self.preview_note_space.pack_forget()

    def _reveal(self):
        """Map the fixed layout immediately, independently of PDF preparation."""
        if self.window is not None and self.window.state() == "withdrawn":
            self.window.update_idletasks()
            self.window.deiconify()
            self.window.grab_set()
            self.window.focus_set()

    def _preview_failed(self):
        """Keep settings usable if the preview process could not return an image."""
        if self.window is not None:
            self._set_preview_note(self._text("flt_preview_unavailable"))
            self._reveal()

    def apply_changes(self):
        """Validate the complete draft before publishing any setting."""
        if self.window is None or self._busy or getattr(self.app, "output_options_locked", False):
            return
        draft = self._draft()
        if draft["marking_mode"] != MarkingMode.ALL_MATCHES and not parse_names(draft["names"]):
            show_error(self.app, self._text("error"), self._text("flt_names_required"))
            return
        # Publish settings together so variable traces never persist a partial draft.
        self.app.app_settings.update_settings(draft)
        snapshot = self.app.app_settings.snapshot()
        self.app.sync_setting_variables({key: snapshot[key] for key in draft})
        self.close()

    def _on_destroy(self, event):
        if event.widget is self.window:
            self._cleanup()
            self.window = None

    def _cleanup(self):
        if self._preview is not None:
            self._preview.detach()
            self._preview = None
        for variable, trace in self._external_traces:
            variable.trace_remove("write", trace)
        self._external_traces.clear()
        self._photo = None
        self._photos = []

    def close(self):
        """Discard draft values and detach from the app-owned preview worker."""
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
