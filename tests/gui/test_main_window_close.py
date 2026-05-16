from types import SimpleNamespace

from src.gui.main_window import PDFHighlighterApp


class FakeWindow:
    def __init__(self):
        self.destroyed = False

    def winfo_exists(self):
        return not self.destroyed

    def destroy(self):
        self.destroyed = True


class FakeRoot(FakeWindow):
    pass


class FakePreview:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_close_closes_owned_windows_before_root():
    app = PDFHighlighterApp.__new__(PDFHighlighterApp)
    app.root = FakeRoot()
    app.preview_window_handler = FakePreview()
    app.filter_dialog = SimpleNamespace(window=FakeWindow())
    app.watermark_dialog = SimpleNamespace(window=FakeWindow())
    app.dev_tools = SimpleNamespace(window=FakeWindow())

    app.close()

    assert app.preview_window_handler.closed
    assert app.filter_dialog.window.destroyed
    assert app.watermark_dialog.window.destroyed
    assert app.dev_tools.window.destroyed
    assert app.root.destroyed
