from src.gui.main_window import PDFHighlighterApp
from src.utils.theme import get_theme_colors


class FakeWindow:
    def __init__(self):
        self.children = []

    def winfo_children(self):
        return self.children


def test_apply_theme_to_window_updates_native_title_bar(monkeypatch):
    app = PDFHighlighterApp.__new__(PDFHighlighterApp)
    app._current_effective_theme = "dark"
    app._current_theme_colors = get_theme_colors("dark")
    calls = []
    monkeypatch.setattr(
        "src.gui.main_window.set_windows_title_bar_theme",
        lambda window, effective_theme: calls.append((window, effective_theme)),
    )
    app._configure_classic_widget = lambda window, colors: None
    app._apply_theme_to_widget_tree = lambda child, colors: None
    window = FakeWindow()

    app.apply_theme_to_window(window)

    assert getattr(window, "_hsph_effective_theme") == "dark"
    assert calls == [(window, "dark")]
