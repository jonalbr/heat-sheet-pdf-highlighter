from types import SimpleNamespace

from PIL import Image

from src.gui.preview import PreviewWindow, _page_to_image, _render_watermark_layer


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

    preview.preview_watermark(1, "TXT", "#FF0000", 16, "top", 0.5, 0.05, 1, force_open=False)

    assert errors == []


def test_explicit_preview_without_pdf_still_reports_error(monkeypatch):
    errors = []
    app = SimpleNamespace(strings={"error": "Error", "val_pdf_first": "Select a PDF first"})
    preview = PreviewWindow(app)
    monkeypatch.setattr("src.gui.preview.show_error", lambda *args: errors.append(args))

    preview.preview_watermark(1, "TXT", "#FF0000", 16, "top", 0.5, 0.05, 1, force_open=True)

    assert len(errors) == 1


class FakeWindow:
    def __init__(self):
        self.destroyed = False

    def winfo_exists(self):
        return not self.destroyed

    def destroy(self):
        self.destroyed = True


class StaleWindow:
    def winfo_exists(self):
        raise RuntimeError("window is gone")


def test_close_destroys_preview_window():
    preview = PreviewWindow(SimpleNamespace())
    preview.window = FakeWindow()
    preview.canvas = object()
    preview.page_item = 1
    preview.watermark_item = 2
    preview.page_photo = object()
    preview.watermark_photo = object()

    preview.close()

    assert preview.window is None
    assert preview.canvas is None
    assert preview.page_item is None
    assert preview.watermark_item is None
    assert preview.page_photo is None
    assert preview.watermark_photo is None


def test_close_tolerates_already_destroyed_preview_window():
    preview = PreviewWindow(SimpleNamespace())
    preview.window = StaleWindow()
    preview.canvas = object()

    preview.close()

    assert preview.window is None
    assert preview.canvas is None


def test_render_watermark_layer_returns_separate_overlay():
    layer, position = _render_watermark_layer((200, 100), "TXT", 10, "#FF0000", "custom", 0.25, 0.75)

    assert layer is not None
    assert layer.mode == "RGBA"
    assert layer.size[0] > 0
    assert layer.size[1] > 0
    assert position[0] >= 0
    assert position[1] >= 0


class FakeCanvas:
    def __init__(self):
        self.configured = {}
        self.items = {1: {}, 2: {}}
        self.coords_calls = []
        self.created = []

    def configure(self, **kwargs):
        self.configured.update(kwargs)

    def itemconfigure(self, item_id, **kwargs):
        self.items.setdefault(item_id, {}).update(kwargs)

    def create_image(self, *args, **kwargs):
        self.created.append((args, kwargs))
        return 2

    def coords(self, item_id, *coords):
        self.coords_calls.append((item_id, coords))


def test_update_canvas_scene_moves_existing_watermark_without_flattening(monkeypatch):
    preview = PreviewWindow(SimpleNamespace())
    preview.canvas = FakeCanvas()
    preview.page_item = 1
    preview.watermark_item = 2
    monkeypatch.setattr("src.gui.preview.ImageTk.PhotoImage", lambda image: f"photo-{image.size}")

    preview._update_canvas_scene(Image.new("RGB", (200, 100), "white"), Image.new("RGBA", (10, 5)), (40, 70))

    assert preview.canvas.configured == {"width": 200, "height": 100}
    assert preview.canvas.items[1]["image"] == "photo-(200, 100)"
    assert preview.canvas.items[2]["image"] == "photo-(10, 5)"
    assert preview.canvas.coords_calls == [(2, (40, 70))]


def test_update_canvas_scene_hides_watermark_when_disabled(monkeypatch):
    preview = PreviewWindow(SimpleNamespace())
    preview.canvas = FakeCanvas()
    preview.page_item = 1
    preview.watermark_item = 2
    monkeypatch.setattr("src.gui.preview.ImageTk.PhotoImage", lambda image: f"photo-{image.size}")

    preview._update_canvas_scene(Image.new("RGB", (200, 100), "white"), None, None)

    assert preview.canvas.items[2]["state"] == "hidden"
    assert preview.watermark_photo is None


def test_get_page_image_reuses_cached_base_page(monkeypatch, tmp_path):
    opens = []
    page_image = Image.new("RGB", (200, 100), "white")

    class FakeDocument:
        def __init__(self, path):
            opens.append(path)

        def __len__(self):
            return 1

        def __getitem__(self, index):
            return object()

        def close(self):
            pass

    app = SimpleNamespace(input_file_full_path=str(tmp_path / "preview.pdf"))
    preview = PreviewWindow(app)
    monkeypatch.setattr("src.gui.preview.Document", FakeDocument)
    monkeypatch.setattr("src.gui.preview._page_to_image", lambda page: page_image)

    first, first_count = preview._get_page_image(1)
    second, second_count = preview._get_page_image(1)

    assert first is page_image
    assert second is page_image
    assert first_count == second_count == 1
    assert len(opens) == 1
