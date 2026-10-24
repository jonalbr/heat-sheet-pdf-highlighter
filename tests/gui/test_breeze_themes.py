"""Exercise the vendored themes with the interpreter's actual Tcl/Tk runtime."""

from pathlib import Path
import tkinter as tk
from tkinter import ttk

import pytest


@pytest.mark.parametrize("theme_name", ["breeze", "breeze-dark"])
@pytest.mark.parametrize("individual_theme", [False, True])
def test_breeze_theme_loads_in_running_tk(theme_name, individual_theme):
    """Catch Tcl/Tk version conflicts in both supported package entry points."""
    try:
        root = tk.Tk()
    except tk.TclError as error:
        if "no display name" in str(error) or "couldn't connect to display" in str(error):
            pytest.skip("A display is required to load Tk themes")
        raise

    try:
        root.withdraw()
        theme_path = Path(__file__).resolve().parents[2] / "assets" / "tkBreeze"
        if individual_theme:
            theme_path /= theme_name
        root.tk.call("lappend", "auto_path", str(theme_path))
        root.tk.call("package", "require", f"ttk::theme::{theme_name}")
        style = ttk.Style(root)
        style.theme_use(theme_name)
        for widget_type in (ttk.Button, ttk.Entry, ttk.Checkbutton, ttk.Combobox, ttk.Scrollbar):
            widget_type(root).pack()
        root.update_idletasks()
        assert style.theme_use() == theme_name
    finally:
        root.destroy()
