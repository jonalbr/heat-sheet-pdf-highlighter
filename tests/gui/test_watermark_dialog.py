from types import SimpleNamespace

from src.gui.dialogs import WatermarkDialog


class FakeWindow:
    def __init__(self):
        self.destroyed = False

    def winfo_exists(self):
        return not self.destroyed

    def destroy(self):
        self.destroyed = True


class FakePreview:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_close_can_also_close_preview():
    app = SimpleNamespace(root=object(), preview_window_handler=FakePreview())
    dialog = WatermarkDialog(app)
    dialog.window = FakeWindow()

    dialog.close(close_preview=True)

    assert app.preview_window_handler.closed
    assert dialog.window is None
