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



def test_language_refresh_keeps_raw_draft_values_without_publishing(monkeypatch):
    app = SimpleNamespace(root=object(), preview_window_handler=FakePreview())
    dialog = WatermarkDialog(app)
    dialog.window = FakeWindow()
    draft = dict(enabled=1, text="Uncommitted", color="#123456", size="unfinished", position="custom",
                 x_ratio=0.25, y_ratio=0.75)
    dialog._draft_vars = {key: SimpleNamespace(get=lambda value=value: value) for key, value in draft.items()}
    restored = []
    monkeypatch.setattr(dialog, "open", lambda value: restored.append(value))
    dialog.refresh_ui_strings()
    assert restored == [draft]
    assert not app.preview_window_handler.closed


def test_busy_stops_position_repeat_without_discarding_draft():
    dialog = WatermarkDialog(SimpleNamespace(root=object()))
    stopped = []
    dialog._stop_nudge = lambda: stopped.append(True)
    dialog.set_busy(True)
    assert dialog._busy
    assert stopped == [True]



def test_busy_apply_neither_reads_nor_publishes_draft():
    dialog = WatermarkDialog(SimpleNamespace(root=object(), output_options_locked=True))
    dialog._draft = lambda: (_ for _ in ()).throw(AssertionError("Read busy draft"))
    dialog.apply_changes()


def test_apply_publishes_preserved_draft_after_unlock(monkeypatch):
    settings = {}
    app = SimpleNamespace(root=object(), output_options_locked=False,
                          app_settings=SimpleNamespace(update_settings=settings.update))
    dialog = WatermarkDialog(app)
    dialog._draft_active = True
    draft = dict(enabled=1, text="Draft", color="#123456", size="24", position="custom", x_ratio=0.2, y_ratio=0.3)
    dialog._draft_vars = {key: SimpleNamespace(get=lambda value=value: value) for key, value in draft.items()}
    dialog._stop_nudge = lambda: None
    closed = []
    monkeypatch.setattr(dialog, "close", lambda: closed.append(True))
    dialog.set_busy(True)
    dialog.apply_changes()
    assert settings == {}
    dialog.set_busy(False)
    dialog.apply_changes()
    assert settings == dict(watermark_enabled="True", watermark_text="Draft", watermark_color="#123456", watermark_size=24,
                            watermark_position="custom", watermark_x_ratio=0.2, watermark_y_ratio=0.3)
    assert closed == [True]
