"""
Preview window functionality
"""

import logging
from pathlib import Path
from tkinter import Canvas, Toplevel, ttk
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont, ImageTk
from pymupdf import Document

from ..core.watermark import calculate_text_position
from .message_dialog import show_error
from .ui_strings import get_ui_string

if TYPE_CHECKING:
    from src.gui.main_window import PDFHighlighterApp


def _page_to_image(page):
    """Render a PyMuPDF page to a Pillow image for preview display."""
    pix = page.get_pixmap()
    mode = "RGBA" if getattr(pix, "alpha", False) else "RGB"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


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
    if not text:
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
    position_xy = calculate_text_position(page_size[0], page_size[1], text_width, text_height, position, x_ratio, y_ratio)
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

        self._cached_source_path: Path | None = None
        self._cached_page_number: int | None = None
        self._cached_page_count: int | None = None
        self._cached_page_image: Image.Image | None = None

    def preview_watermark(self, enabled, text, color, size, position, x_ratio, y_ratio, preview_page, origin=None, force_open=True):
        """Show watermark preview."""
        if not force_open and not self.is_open():
            return

        if not hasattr(self.app, "input_file_full_path"):
            if force_open:
                show_error(self.app, get_ui_string(self.app.strings, "error"), get_ui_string(self.app.strings, "val_pdf_first"))
            return

        try:
            page_image, page_count = self._get_page_image(preview_page)
            if preview_page == page_count + 1:
                self.change_page(-1)  # Go back to last page
                return
            if preview_page > page_count or preview_page < 1:
                self.change_page(0, reset=True)  # Reset to first page
                return

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
            self.current_page = preview_page

            watermark_layer, watermark_position = (None, None)
            if enabled and text:
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
                self.window.lift()
                if force_open:
                    self.window.focus_set()  # Only force focus when preview is explicitly opened
                self._update_canvas_scene(page_image, watermark_layer, watermark_position)
            elif force_open:
                self._create_preview_window(page_image, watermark_layer, watermark_position)
        except Exception as e:
            logging.getLogger("preview").exception("Error previewing watermark: %s", e)
            show_error(self.app, get_ui_string(self.app.strings, "error"), str(e))

    def _get_page_image(self, preview_page: int) -> tuple[Image.Image, int]:
        """Return the cached base page image when possible; render only when the page changes."""
        source_path = Path(self.app.input_file_full_path)
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
            if preview_page < 1 or preview_page > page_count:
                # The caller needs the page count to decide how to recover.
                return self._cached_page_image or Image.new("RGB", (1, 1), "white"), page_count

            page_image = _page_to_image(document[preview_page - 1])
        finally:
            document.close()

        self._cached_source_path = source_path
        self._cached_page_number = preview_page
        self._cached_page_count = page_count
        self._cached_page_image = page_image
        return page_image, page_count

    def _update_canvas_scene(self, page_image: Image.Image, watermark_layer: Image.Image | None, watermark_position):
        """Update the existing canvas without flattening the watermark into the page image."""
        if self.canvas is None:
            return

        self.page_photo = ImageTk.PhotoImage(page_image)
        self.canvas.configure(width=page_image.width, height=page_image.height)
        self.canvas.itemconfigure(self.page_item, image=self.page_photo)

        if watermark_layer is None or watermark_position is None:
            if self.watermark_item is not None:
                self.canvas.itemconfigure(self.watermark_item, state="hidden")
            self.watermark_photo = None
            return

        self.watermark_photo = ImageTk.PhotoImage(watermark_layer)
        if self.watermark_item is None:
            self.watermark_item = self.canvas.create_image(*watermark_position, anchor="nw", image=self.watermark_photo)
            return

        self.canvas.itemconfigure(self.watermark_item, image=self.watermark_photo, state="normal")
        self.canvas.coords(self.watermark_item, *watermark_position)

    def is_open(self) -> bool:
        """Return whether the preview window currently exists."""
        try:
            return bool(self.window and self.window.winfo_exists())
        except Exception:
            return False

    def close(self) -> None:
        """Close the preview window if it is open."""
        if self.is_open():
            self.window.destroy()
        self.window = None
        self.canvas = None
        self.page_item = None
        self.watermark_item = None
        self.page_photo = None
        self.watermark_photo = None

    def _create_preview_window(self, page_image: Image.Image, watermark_layer: Image.Image | None, watermark_position):
        """Create the preview window."""
        self.window = Toplevel(self.app.root)
        self.window.title(get_ui_string(self.app.strings, "wm_preview_window"))
        self.app.apply_theme_to_window(self.window)

        # Position next to the origin window
        if self.last_origin:
            ox = self.last_origin.winfo_rootx()
            oy = self.last_origin.winfo_rooty()
            ow = self.last_origin.winfo_width()
            self.window.geometry(f"+{ox + ow + 10}+{oy}")

        self.window.transient(None)
        self.window.grab_release()
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.canvas = Canvas(self.window, width=page_image.width, height=page_image.height, highlightthickness=0, borderwidth=0)
        self.canvas.pack()
        self.page_photo = ImageTk.PhotoImage(page_image)
        self.page_item = self.canvas.create_image(0, 0, anchor="nw", image=self.page_photo)
        if watermark_layer is not None and watermark_position is not None:
            self.watermark_photo = ImageTk.PhotoImage(watermark_layer)
            self.watermark_item = self.canvas.create_image(*watermark_position, anchor="nw", image=self.watermark_photo)

        # Navigation buttons frame
        nav_frame = ttk.Frame(self.window)
        nav_frame.pack(pady=5)

        prev_btn = ttk.Button(nav_frame, text=get_ui_string(self.app.strings, "nav_prev"), command=lambda: self.change_page(-1))
        prev_btn.pack(side="left", padx=5)

        next_btn = ttk.Button(nav_frame, text=get_ui_string(self.app.strings, "nav_next"), command=lambda: self.change_page(1))
        next_btn.pack(side="left", padx=5)
        self.app.apply_theme_to_window(self.window)

    def change_page(self, delta: int, reset: bool = False):
        """Change preview page."""
        if reset:
            self.current_page = 1
        else:
            self.current_page = max(1, self.current_page + delta)

        # Re-call preview_watermark with stored settings
        if self.last_watermark_data:
            self.preview_watermark(
                self.last_watermark_data["enabled"],
                self.last_watermark_data["text"],
                self.last_watermark_data["color"],
                self.last_watermark_data["size"],
                self.last_watermark_data["position"],
                self.last_watermark_data["x_ratio"],
                self.last_watermark_data["y_ratio"],
                self.current_page,
                origin=self.last_origin,
            )
