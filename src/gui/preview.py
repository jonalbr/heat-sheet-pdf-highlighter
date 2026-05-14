"""
Preview window functionality
"""

import logging
from tkinter import Toplevel, ttk
from typing import TYPE_CHECKING

from PIL import Image, ImageTk
from pymupdf import Document

from ..core.watermark import overlay_watermark_on_image
from .message_dialog import show_error
from .ui_strings import get_ui_string

if TYPE_CHECKING:
    from src.gui.main_window import PDFHighlighterApp


def _page_to_image(page):
    """Render a PyMuPDF page to a Pillow image for preview display."""
    pix = page.get_pixmap()
    mode = "RGBA" if getattr(pix, "alpha", False) else "RGB"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


class PreviewWindow:
    """Handles watermark preview functionality."""

    def __init__(self, app_instance: "PDFHighlighterApp"):
        self.app = app_instance
        self.window = None
        self.current_page = 1
        self.last_watermark_data = {}
        self.last_origin = None

    def preview_watermark(self, enabled, text, color, size, position, preview_page, origin=None, force_open=True):
        """Show watermark preview."""
        if not hasattr(self.app, "input_file_full_path"):
            show_error(self.app, get_ui_string(self.app.strings, "error"), get_ui_string(self.app.strings, "val_pdf_first"))
            return

        try:
            document = Document(self.app.input_file_full_path)
            if preview_page == len(document) + 1:
                self.change_page(-1)  # Go back to last page
                document.close()
                return
            elif preview_page > len(document) or preview_page < 1:
                self.change_page(0, reset=True)  # Reset to first page
                document.close()
                return

            page = document[preview_page - 1]
            image = _page_to_image(page)
            if enabled and text:
                image = overlay_watermark_on_image(image, text, size, color, position)

            # Save last settings and origin for dynamic updates and navigation
            self.last_watermark_data = {"enabled": enabled, "text": text, "color": color, "size": size, "position": position}
            self.last_origin = origin if origin else self.app.root
            self.current_page = preview_page

            # If preview_window exists, update image; otherwise, only open window if force_open is True.
            if self.window and self.window.winfo_exists():
                self.window.lift()
                if force_open:
                    self.window.focus_set()  # Only force focus when preview is explicitly opened
                img_tk = ImageTk.PhotoImage(image)
                if hasattr(self, "image_label"):
                    self.image_label.configure(image=img_tk)
                    setattr(self.image_label, "image", img_tk)  # Store a reference to the image
            else:
                if force_open:
                    self._create_preview_window(image)

            document.close()
        except Exception as e:
            logging.getLogger("preview").exception("Error previewing watermark: %s", e)
            show_error(self.app, get_ui_string(self.app.strings, "error"), str(e))

    def _create_preview_window(self, image):
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
        self.window.protocol("WM_DELETE_WINDOW", self.window.destroy)

        img_tk = ImageTk.PhotoImage(image)
        self.image_label = ttk.Label(self.window, image=img_tk, style="Logo.TLabel")
        setattr(self.image_label, "image", img_tk)  # Store a reference to the image
        self.image_label.pack()

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
                self.current_page,
                origin=self.last_origin,
            )
