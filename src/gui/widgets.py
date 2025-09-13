"""
Custom widgets
"""

from tkinter import Toplevel, Label, Widget


class Tooltip:
    """
    Create a tooltip for a given widget.
    """

    def __init__(self, widget: Widget, text: str):
        self.widget = widget
        self.text = text
        self.tooltip_window = None
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, event=None):
        """Show the tooltip."""
        if self.tooltip_window or not self.text:
            return
        x = self.widget.winfo_rootx() + 25
        y = self.widget.winfo_rooty() + 25
        self.tooltip_window = tw = Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = Label(tw, text=self.text, justify="left", background="#ffffe0", relief="solid", borderwidth=1, font=("tahoma", 8, "normal"))
        label.pack(ipadx=1)

    def hide_tip(self, event=None):
        """Hide the tooltip."""
        tw = self.tooltip_window
        self.tooltip_window = None
        if tw:
            tw.destroy()
