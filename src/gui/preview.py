"""
Preview window functionality
"""

import logging
from contextlib import suppress
from pathlib import Path
from tkinter import BooleanVar, Canvas, StringVar, TclError, Toplevel, ttk
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont, ImageTk
from pymupdf import Document

from ..core.watermark import calculate_text_position
from .message_dialog import show_error
from .ui_strings import get_ui_string
from .widgets import scrollbar_command

if TYPE_CHECKING:
    from src.gui.main_window import PDFHighlighterApp


def _page_to_image(page):
    """Render a PyMuPDF page to a Pillow image for preview display."""
    pix = page.get_pixmap()
    mode = "RGBA" if getattr(pix, "alpha", False) else "RGB"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def _view_transform(page_size, viewport, fit):
    """Return one display scale and centering offset, leaving PDF coordinates intact."""
    width, height = page_size
    available_width, available_height = viewport
    scale = min(available_width / width, available_height / height) if fit else 1.0
    return scale, (max(0, (available_width - width * scale) / 2), max(0, (available_height - height * scale) / 2))


def _render_watermark_layer(
    page_size: tuple[int, int],
    text: str,
    font_size: int,
    color_hex: str,
    position: str,
    x_ratio: float,
    y_ratio: float,
) -> tuple[Image.Image | None, tuple[float, float] | None]:
    """Render the watermark as its own transparent image and return its canvas position."""
    if not text.strip():
        return None, None

    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()

    measure_image = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    measure_draw = ImageDraw.Draw(measure_image, "RGBA")
    bbox = measure_draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    if text_width <= 0 or text_height <= 0:
        return None, None

    layer = Image.new("RGBA", (text_width, text_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    draw.text((-bbox[0], -bbox[1]), text, font=font, fill=color_hex)
    position_xy = calculate_text_position(
        page_size[0], page_size[1], text_width, text_height, position, x_ratio, y_ratio
    )
    return layer, position_xy


class PreviewWindow:
    """Handles watermark preview functionality."""

    def __init__(self, app_instance: "PDFHighlighterApp"):
        self.app = app_instance
        self.window = None
        self.current_page = 1
        self.last_watermark_data = {}
        self.last_origin = None

        self.canvas = None
        self.page_item = None
        self.watermark_item = None
        self.page_photo = None
        self.watermark_photo = None
        self.fit_var = None
        self.page_var = None
        self._resize_timer = None
        self._scene = None
        self._display_page_key = None
        self._page_error = False

        self._cached_source_path: Path | None = None
        self._cached_page_number: int | None = None
        self._cached_page_count: int | None = None
        self._cached_page_image: Image.Image | None = None

    def preview_watermark(
        self, enabled, text, color, size, position, x_ratio, y_ratio, preview_page, origin=None, force_open=True
    ):
        """Show watermark preview."""
        if not force_open and not self.is_open():
            return

        if not hasattr(self.app, "input_file_full_path"):
            if force_open:
                show_error(
                    self.app, get_ui_string(self.app.strings, "error"), get_ui_string(self.app.strings, "val_pdf_first")
                )
            return

        try:
            page_image, page_count = self._get_page_image(preview_page)

            # Save last settings and origin for dynamic updates and navigation
            self.last_watermark_data = {
                "enabled": enabled,
                "text": text,
                "color": color,
                "size": size,
                "position": position,
                "x_ratio": x_ratio,
                "y_ratio": y_ratio,
            }
            self.last_origin = origin if origin else self.app.root
            self.current_page = self._cached_page_number if page_count else 0

            watermark_layer, watermark_position = (None, None)
            if page_image is not None and enabled and text:
                watermark_layer, watermark_position = _render_watermark_layer(
                    page_image.size,
                    text,
                    size,
                    color,
                    position,
                    x_ratio,
                    y_ratio,
                )

            if self.is_open():
                if force_open:
                    self.window.lift()
                    self.window.focus_set()  # Only force focus when preview is explicitly opened
                self._update_canvas_scene(page_image, watermark_layer, watermark_position)
            elif force_open:
                self._create_preview_window(page_image, watermark_layer, watermark_position)
            self._page_error = False
            self.refresh_ui_strings()
        except Exception as e:
            logging.getLogger("preview").exception("Error previewing watermark: %s", e)
            show_error(
                self.app,
                get_ui_string(self.app.strings, "error"),
                get_ui_string(self.app.strings, "preview_failed"),
            )

    def _get_page_image(self, preview_page: int) -> tuple[Image.Image | None, int]:
        """Return the cached base page image when possible; render only when the page changes."""
        source_path = Path(self.app.input_file_full_path)
        if self._cached_source_path == source_path and self._cached_page_count:
            preview_page = max(1, min(preview_page, self._cached_page_count))
        cache_matches = (
            self._cached_source_path == source_path
            and self._cached_page_number == preview_page
            and self._cached_page_count is not None
            and self._cached_page_image is not None
        )
        if cache_matches:
            return self._cached_page_image, self._cached_page_count

        document = Document(source_path)
        try:
            page_count = len(document)
            preview_page = max(1, min(preview_page, page_count)) if page_count else 0
            page_image = _page_to_image(document[preview_page - 1]) if page_count else None
        finally:
            document.close()

        self._cached_source_path = source_path
        self._cached_page_number = preview_page
        self._cached_page_count = page_count
        self._cached_page_image = page_image
        return page_image, page_count

    def _update_canvas_scene(self, page_image, watermark_layer, watermark_position):
        """Keep unscaled source layers; redraw without flattening or reopening the PDF."""
        self._scene = (page_image, watermark_layer, watermark_position)
        self._draw_scene()

    def _draw_scene(self):
        """Apply a shared display transform to both independently cached layers."""
        if self._resize_timer is not None:
            self.window.after_cancel(self._resize_timer)
        self._resize_timer = None
        if self.canvas is None or self._scene is None:
            return
        page_image, watermark_layer, position = self._scene
        if page_image is None:
            for item in (self.page_item, self.watermark_item):
                if item is not None:
                    self.canvas.itemconfigure(item, state="hidden")
            self.page_photo = self.watermark_photo = None
            self._display_page_key = None
            self.canvas.configure(scrollregion=(0, 0, 0, 0))
            return
        viewport = (self.canvas.winfo_width(), self.canvas.winfo_height())
        if min(viewport) <= 1:
            return  # Wait for the first real canvas allocation.
        scale, offset = _view_transform(page_image.size, viewport, bool(self.fit_var and self.fit_var.get()))
        scaled_size = tuple(max(1, round(value * scale)) for value in page_image.size)
        key = (id(page_image), scaled_size)
        if key != self._display_page_key:
            scaled = (
                page_image
                if scaled_size == page_image.size
                else page_image.resize(scaled_size, Image.Resampling.LANCZOS)
            )
            self.page_photo = ImageTk.PhotoImage(scaled)
            self._display_page_key = key
        self.canvas.itemconfigure(self.page_item, image=self.page_photo, state="normal")
        self.canvas.coords(self.page_item, *offset)
        self.canvas.configure(scrollregion=(0, 0, max(viewport[0], scaled_size[0]), max(viewport[1], scaled_size[1])))
        if watermark_layer is None or position is None:
            if self.watermark_item is not None:
                self.canvas.itemconfigure(self.watermark_item, state="hidden")
            self.watermark_photo = None
            return
        size = tuple(max(1, round(value * scale)) for value in watermark_layer.size)
        layer = (
            watermark_layer if size == watermark_layer.size else watermark_layer.resize(size, Image.Resampling.LANCZOS)
        )
        self.watermark_photo = ImageTk.PhotoImage(layer)
        xy = tuple(origin + coordinate * scale for origin, coordinate in zip(offset, position))
        if self.watermark_item is None:
            self.watermark_item = self.canvas.create_image(*xy, anchor="nw", image=self.watermark_photo)
        else:
            self.canvas.itemconfigure(self.watermark_item, image=self.watermark_photo, state="normal")
            self.canvas.coords(self.watermark_item, *xy)

    def _schedule_resize(self, _event=None):
        """Coalesce consecutive canvas allocations into one redraw."""
        if self._resize_timer is not None:
            self.window.after_cancel(self._resize_timer)
        self._resize_timer = self.window.after(60, self._draw_scene)

    def _change_fit(self):
        """Reset scrolling when switching between fit and original display size."""
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self._schedule_resize()

    def is_open(self) -> bool:
        """Return whether the preview window currently exists."""
        try:
            return bool(self.window and self.window.winfo_exists())
        except Exception:
            return False

    def close(self) -> None:
        """Close the preview window if it is open."""
        if self._resize_timer is not None and self.window is not None:
            with suppress(TclError):
                self.window.after_cancel(self._resize_timer)
        self._resize_timer = None
        if self.is_open():
            window, self.window = self.window, None
            window.destroy()
        self.window = None
        self.canvas = None
        self.page_item = None
        self.watermark_item = None
        self.page_photo = None
        self.watermark_photo = None
        self._scene = None
        self._display_page_key = None
        self.fit_var = self.page_var = None
        self._page_error = False
        self.last_watermark_data = {}
        self.last_origin = None

    def _on_destroy(self, event):
        """Release preview images and draft references on direct destruction."""
        if event.widget is self.window:
            self.close()

    def _create_preview_window(self, page_image, watermark_layer, watermark_position):
        """Create a bounded, resizable viewport with navigation outside the canvas."""
        self.window = window = Toplevel(self.app.root)
        window.withdraw()
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Destroy>", self._on_destroy, add="+")
        viewport = ttk.Frame(window)
        viewport.grid(row=0, column=0, sticky="nsew")
        viewport.columnconfigure(0, weight=1)
        viewport.rowconfigure(0, weight=1)
        self.canvas = Canvas(viewport, width=1, height=1, highlightthickness=0, borderwidth=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(viewport, orient="vertical", command=scrollbar_command(self.canvas.yview))
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(viewport, orient="horizontal", command=scrollbar_command(self.canvas.xview))
        horizontal.grid(row=1, column=0, sticky="ew")
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        self.page_item = self.canvas.create_image(0, 0, anchor="nw")
        self.watermark_item = self.canvas.create_image(0, 0, anchor="nw", state="hidden")
        self.canvas.bind("<Configure>", self._schedule_resize)
        self.canvas.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units"))
        nav = ttk.Frame(window, padding=6)
        nav.grid(row=1, column=0, sticky="ew")
        nav.columnconfigure(1, weight=1)
        self.prev_button = ttk.Button(nav, command=lambda: self.change_page(-1))
        self.prev_button.grid(row=0, column=0, padx=4)
        self.page_label = ttk.Label(nav, anchor="center")
        self.page_label.grid(row=0, column=1, padx=4, sticky="ew")
        self.next_button = ttk.Button(nav, command=lambda: self.change_page(1))
        self.next_button.grid(row=0, column=2, padx=4)
        self.page_var = StringVar(window)
        self.page_entry = ttk.Entry(nav, textvariable=self.page_var, width=6)
        self.page_entry.grid(row=1, column=0, padx=4, pady=4)
        self.page_entry.bind("<Return>", self.jump_to_page)
        self.go_button = ttk.Button(nav, command=self.jump_to_page)
        self.go_button.grid(row=1, column=1, sticky="w", padx=4)
        self.fit_var = BooleanVar(window, value=True)
        self.fit_button = ttk.Checkbutton(nav, variable=self.fit_var, command=self._change_fit)
        self.fit_button.grid(row=2, column=0, columnspan=3, sticky="w", padx=4)
        self.page_hint = ttk.Label(nav, wraplength=400)
        self.page_hint.grid(row=3, column=0, columnspan=3, sticky="ew", padx=4)
        nav.bind("<Configure>", self._resize_navigation)
        self.refresh_ui_strings()
        self.app.apply_theme_to_window(window)
        window.update_idletasks()
        screen_width = max(1, window.winfo_screenwidth() - 40)
        screen_height = max(1, window.winfo_screenheight() - 80)
        width = min(screen_width, max(540, nav.winfo_reqwidth(), min(900, page_image.width if page_image else 540)))
        height = min(screen_height, 800)
        window.minsize(min(screen_width, nav.winfo_reqwidth() + 20), min(screen_height, nav.winfo_reqheight() + 100))
        x = min(max(0, self.app.root.winfo_rootx() + 40), max(0, screen_width - width))
        y = min(max(0, self.app.root.winfo_rooty()), max(0, screen_height - height))
        window.geometry(f"{width}x{height}+{x}+{y}")
        window.deiconify()
        window.update_idletasks()
        self._update_canvas_scene(page_image, watermark_layer, watermark_position)

    def _resize_navigation(self, event):
        """Keep translated navigation reachable when fonts or labels grow."""
        self.page_hint.configure(wraplength=max(80, event.width - 12))
        self.window.minsize(
            min(max(1, self.window.winfo_screenwidth() - 40), event.widget.winfo_reqwidth() + 20),
            min(max(1, self.window.winfo_screenheight() - 80), event.widget.winfo_reqheight() + 100),
        )

    def refresh_ui_strings(self):
        """Translate existing controls without rebuilding the draft or viewport."""
        if self.page_var is None:
            return

        def text(key):
            return get_ui_string(self.app.strings, key)

        count = self._cached_page_count or 0
        self.window.title(text("wm_preview_window"))
        self.page_label.configure(text=text("nav_page_count").format(self.current_page, count))
        self.prev_button.configure(text=text("nav_prev"), state="normal" if self.current_page > 1 else "disabled")
        self.next_button.configure(text=text("nav_next"), state="normal" if self.current_page < count else "disabled")
        self.go_button.configure(text=text("nav_go"), state="normal" if count else "disabled")
        self.page_entry.configure(state="normal" if count else "disabled")
        self.fit_button.configure(text=text("nav_fit"))
        hint = text("nav_no_pages") if not count else text("nav_invalid_page").format(count) if self._page_error else ""
        self.page_hint.configure(text=hint)
        if not self._page_error:
            self.page_var.set(str(self.current_page))

    def jump_to_page(self, _event=None):
        """Validate a committed page entry; invalid input never changes the scene."""
        value = self.page_var.get().strip()
        value = value.lstrip("0") or ("0" if value else "")
        count = self._cached_page_count or 0
        if not value.isascii() or not value.isdigit() or len(value) > len(str(count)) or not 1 <= int(value) <= count:
            self._page_error = True
            self.refresh_ui_strings()
            return "break"
        self.change_page(int(value) - self.current_page)
        return "break"

    def change_page(self, delta: int, reset: bool = False):
        """Navigate within bounds using the retained watermark draft."""
        if not self.last_watermark_data:
            return
        count = self._cached_page_count or 0
        if not count:
            return
        target = 1 if reset else max(1, min(count, self.current_page + delta))
        self.preview_watermark(
            **self.last_watermark_data, preview_page=target, origin=self.last_origin, force_open=False
        )
