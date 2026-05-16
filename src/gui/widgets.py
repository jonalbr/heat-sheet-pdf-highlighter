"""
Custom widgets
"""

from contextlib import suppress
from tkinter import Frame, Label, TclError, Toplevel, Widget

from ..utils.theme import get_theme_colors

TOOLTIP_DELAY_MS = 400
TOOLTIP_OFFSET = (14, 18)


def _resolve_widget_theme(widget: Widget) -> str:
    """Return the effective app theme stored on the nearest Tk root."""
    top_theme = None
    try:
        top = widget.winfo_toplevel()
        top_theme = getattr(top, "_hsph_effective_theme", None)
    except (AttributeError, TclError):
        top_theme = None
    if top_theme:
        return top_theme

    try:
        root = widget._root()  # type: ignore[attr-defined]
        return getattr(root, "_hsph_effective_theme", "light")
    except Exception:
        return "light"


class Tooltip:
    """
    Create a tooltip for a given widget.
    """

    def __init__(self, widget: Widget, text: str, delay_ms: int = TOOLTIP_DELAY_MS):
        existing = getattr(widget, "_hsph_tooltip", None)
        if existing is not None:
            existing.text = text
            existing.delay_ms = delay_ms
            self.widget = widget
            self.text = text
            self.tooltip_window = existing.tooltip_window
            return

        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self.tooltip_window = None
        self._after_id = None
        self._last_pointer = None
        setattr(self.widget, "_hsph_tooltip", self)
        self.widget.bind("<Enter>", self._schedule_tip)
        self.widget.bind("<Motion>", self._on_motion)
        self.widget.bind("<Leave>", self.hide_tip)
        self.widget.bind("<ButtonPress>", self.hide_tip)

    def _schedule_tip(self, event=None):
        """Schedule the tooltip after a short hover delay."""
        self._remember_pointer(event)
        self._cancel_scheduled_tip()
        self._after_id = self.widget.after(self.delay_ms, self.show_tip)

    def _on_motion(self, event=None):
        """Track the pointer and move an already visible tooltip with it."""
        self._remember_pointer(event)
        if self.tooltip_window:
            self._move_tip()

    def _remember_pointer(self, event=None):
        if event is not None:
            self._last_pointer = (event.x_root, event.y_root)

    def _cancel_scheduled_tip(self):
        if not self._after_id:
            return
        with suppress(TclError):
            self.widget.after_cancel(self._after_id)
        self._after_id = None

    def show_tip(self, event=None):
        """Show the tooltip."""
        self._after_id = None
        self._remember_pointer(event)
        if self.tooltip_window or not self.text:
            return

        colors = get_theme_colors(_resolve_widget_theme(self.widget))
        self.tooltip_window = tw = Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.configure(background=colors.tooltip_border)
        with suppress(TclError):
            tw.wm_attributes("-topmost", True)
            tw.wm_attributes("-alpha", 0.98)

        frame = Frame(tw, background=colors.tooltip_border, borderwidth=0)
        frame.pack()
        label = Label(
            frame,
            text=self.text,
            justify="left",
            background=colors.tooltip_background,
            foreground=colors.tooltip_foreground,
            borderwidth=0,
            font=("Segoe UI", 9, "normal"),
            padx=10,
            pady=6,
            wraplength=360,
        )
        label.pack(padx=1, pady=1)
        self._move_tip()

    def _move_tip(self):
        if not self.tooltip_window:
            return
        if self._last_pointer is not None:
            x, y = self._last_pointer
            x += TOOLTIP_OFFSET[0]
            y += TOOLTIP_OFFSET[1]
        else:
            x = self.widget.winfo_rootx() + 16
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tooltip_window.wm_geometry(f"+{x}+{y}")

    def hide_tip(self, event=None):
        """Hide the tooltip."""
        self._cancel_scheduled_tip()
        tw = self.tooltip_window
        self.tooltip_window = None
        if tw:
            tw.destroy()
