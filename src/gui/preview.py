"""
Preview window functionality
"""
from tkinter import Label, Toplevel, messagebox, ttk
from typing import TYPE_CHECKING

from PIL import Image, ImageTk
from pymupdf import Document, utils

from ..core.watermark import overlay_watermark_on_image
from .ui_strings import get_ui_string

if TYPE_CHECKING:
    from src.gui.main_window import PDFHighlighterApp

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
            messagebox.showerror(get_ui_string(self.app.strings, "error"), get_ui_string(self.app.strings, "Please select a PDF first for preview."))
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
            pix = utils.get_pixmap(page)

            image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
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
                # Find and update the image label without changing widget focus.
                for widget in self.window.winfo_children():
                    if isinstance(widget, Label):
                        widget.configure(image=img_tk)
                        setattr(widget, "image", img_tk)  # Store a reference to the image
                        break
            else:
                if force_open:
                    self._create_preview_window(image)
                    
            document.close()
        except Exception as e:
            messagebox.showerror(get_ui_string(self.app.strings, "error"), str(e))

    def _create_preview_window(self, image):
        """Create the preview window."""
        self.window = Toplevel()
        self.window.title(get_ui_string(self.app.strings, "Watermark Preview"))
        
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
        img_label = Label(self.window, image=img_tk)
        setattr(img_label, "image", img_tk)  # Store a reference to the image
        img_label.pack()
        
        # Navigation buttons frame
        nav_frame = ttk.Frame(self.window)
        nav_frame.pack(pady=5)

        prev_btn = ttk.Button(nav_frame, text=get_ui_string(self.app.strings, "Previous Page"), command=lambda: self.change_page(-1))
        prev_btn.pack(side="left", padx=5)

        next_btn = ttk.Button(nav_frame, text=get_ui_string(self.app.strings, "Next Page"), command=lambda: self.change_page(1))
        next_btn.pack(side="left", padx=5)

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
