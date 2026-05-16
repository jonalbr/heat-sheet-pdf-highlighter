from types import SimpleNamespace

from src.gui.preview import PreviewWindow, _page_to_image


class FakePixmap:
    def __init__(self, samples, alpha=False):
        self.width = 1
        self.height = 1
        self.samples = samples
        self.alpha = alpha


class FakePage:
    def __init__(self, pixmap):
        self.pixmap = pixmap
        self.called = False

    def get_pixmap(self):
        self.called = True
        return self.pixmap


def test_page_to_image_uses_current_pymupdf_page_api():
    page = FakePage(FakePixmap(bytes([10, 20, 30])))

    image = _page_to_image(page)

    assert page.called
    assert image.mode == "RGB"
    assert image.size == (1, 1)
    assert image.getpixel((0, 0)) == (10, 20, 30)


def test_page_to_image_handles_alpha_pixmaps():
    page = FakePage(FakePixmap(bytes([10, 20, 30, 40]), alpha=True))

    image = _page_to_image(page)

    assert image.mode == "RGBA"
    assert image.getpixel((0, 0)) == (10, 20, 30, 40)


def test_dynamic_preview_update_without_open_window_or_pdf_is_silent(monkeypatch):
    errors = []
    app = SimpleNamespace(strings={"error": "Error", "val_pdf_first": "Select a PDF first"})
    preview = PreviewWindow(app)
    monkeypatch.setattr("src.gui.preview.show_error", lambda *args: errors.append(args))

    preview.preview_watermark(1, "TXT", "#FF0000", 16, "top", 1, force_open=False)

    assert errors == []


def test_explicit_preview_without_pdf_still_reports_error(monkeypatch):
    errors = []
    app = SimpleNamespace(strings={"error": "Error", "val_pdf_first": "Select a PDF first"})
    preview = PreviewWindow(app)
    monkeypatch.setattr("src.gui.preview.show_error", lambda *args: errors.append(args))

    preview.preview_watermark(1, "TXT", "#FF0000", 16, "top", 1, force_open=True)

    assert len(errors) == 1


class FakeWindow:
    def __init__(self):
        self.destroyed = False

    def winfo_exists(self):
        return not self.destroyed

    def destroy(self):
        self.destroyed = True


def test_close_destroys_preview_window():
    preview = PreviewWindow(SimpleNamespace())
    preview.window = FakeWindow()

    preview.close()

    assert preview.window is None
