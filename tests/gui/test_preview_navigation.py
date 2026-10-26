"""Page navigation, shared display transforms and PDF-position invariance."""

from types import SimpleNamespace

import pytest
from PIL import Image
from pymupdf import Document

from test_settings_reset import app as app
from test_preview import FakeCanvas
from src.core.watermark import watermark_pdf_page
from src.gui.preview import PreviewWindow, _view_transform


def open_preview(app, tmp_path, sizes=((400, 600), (700, 300), (250, 250))):
    source = tmp_path / "mixed.pdf"
    with Document() as doc:
        for width, height in sizes:
            doc.new_page(width=width, height=height)
        doc.save(source)
    app.input_file_full_path = str(source)
    preview = app.preview_window_handler
    preview.preview_watermark(1, "WATERMARK", "#FF0000", 18, "custom", 0.2, 0.7, 1)
    app.root.update_idletasks()
    return preview


def test_navigation_count_boundaries_direct_jump_and_page_sizes(app, tmp_path):
    app.on_language_change("en")
    preview = open_preview(app, tmp_path)
    assert preview.fit_var.get()
    assert preview.page_label.cget("text") == "Page 1 of 3"
    assert str(preview.prev_button.cget("state")) == "disabled"
    preview.change_page(-10)
    assert preview.current_page == 1
    preview.page_var.set("3")
    preview.go_button.invoke()
    assert preview.current_page == 3
    assert preview._cached_page_image.size == (250, 250)
    assert str(preview.next_button.cget("state")) == "disabled"
    preview.change_page(10)
    assert preview.current_page == 3
    preview.page_var.set(" 002 ")
    preview.jump_to_page()
    assert preview._cached_page_image.size == (700, 300)
    assert preview.page_label.cget("text") == "Page 2 of 3"
    preview.change_page(0, reset=True)
    assert preview._cached_page_image.size == (400, 600)


@pytest.mark.parametrize("value", ["", " ", "0", "-1", "4", "1.5", "abc", "１２", "9" * 5000])
def test_invalid_page_entry_preserves_scene_and_gives_translated_hint(app, tmp_path, value):
    app.on_language_change("en")
    preview = open_preview(app, tmp_path)
    image = preview._cached_page_image
    preview.page_var.set(value)
    assert preview.jump_to_page() == "break"
    assert preview.current_page == 1
    assert preview._cached_page_image is image
    assert preview.page_hint.cget("text") == "Enter a page number from 1 to 3."
    app.on_language_change("de")
    assert preview.page_hint.cget("text") == "Bitte eine Seitenzahl von 1 bis 3 eingeben."
    preview.change_page(1)
    assert preview.page_hint.cget("text") == ""
    assert preview.page_var.get() == "2"


def test_single_page_and_source_change_clamp_instead_of_recursing(app, tmp_path):
    preview = open_preview(app, tmp_path)
    preview.change_page(2)
    source = tmp_path / "single.pdf"
    with Document() as doc:
        doc.new_page(width=333, height=444)
        doc.save(source)
    app.input_file_full_path = str(source)
    preview.preview_watermark(**preview.last_watermark_data, preview_page=99)
    assert preview.current_page == 1
    assert preview._cached_page_image.size == (333, 444)
    assert str(preview.prev_button.cget("state")) == "disabled"
    assert str(preview.next_button.cget("state")) == "disabled"


def test_empty_document_has_no_stale_page_and_disables_navigation(app, tmp_path, monkeypatch):
    preview = open_preview(app, tmp_path)

    class EmptyDocument:
        def __init__(self, _path):
            pass

        def __len__(self):
            return 0

        def close(self):
            pass

    monkeypatch.setattr("src.gui.preview.Document", EmptyDocument)
    app.input_file_full_path = str(tmp_path / "empty.pdf")
    preview.preview_watermark(**preview.last_watermark_data, preview_page=3)
    assert preview.current_page == 0
    assert preview.page_photo is None
    assert preview.watermark_photo is None
    assert preview.page_hint.cget("text") == app.strings["nav_no_pages"]
    assert str(preview.go_button.cget("state")) == "disabled"


@pytest.mark.parametrize(
    "size,viewport,fit,scale,offset",
    [
        ((400, 200), (200, 200), True, 0.5, (0, 50)),
        ((200, 400), (400, 200), True, 0.5, (150, 0)),
        ((200, 100), (400, 400), True, 2, (0, 100)),
        ((200, 100), (400, 400), False, 1, (100, 150)),
        ((800, 600), (200, 100), False, 1, (0, 0)),
    ],
)
def test_fit_transform_uses_uniform_scale_and_centers(size, viewport, fit, scale, offset):
    assert _view_transform(size, viewport, fit) == (scale, offset)


def test_scaled_layers_share_transform_and_reuse_originals_and_page_photo(monkeypatch):
    preview = PreviewWindow(SimpleNamespace())
    preview.canvas = FakeCanvas()
    preview.page_item, preview.watermark_item = 1, 2
    preview.fit_var = SimpleNamespace(get=lambda: True)
    photos = []
    monkeypatch.setattr("src.gui.preview.ImageTk.PhotoImage", lambda image: photos.append(image.size) or image.size)
    page = Image.new("RGB", (400, 400), "white")
    layer = Image.new("RGBA", (40, 20), "red")
    original = page.tobytes(), layer.tobytes()
    preview._update_canvas_scene(page, layer, (80, 240))
    assert preview.canvas.items[1]["image"] == (100, 100)
    assert preview.canvas.items[2]["image"] == (10, 5)
    assert preview.canvas.coords_calls[-1] == (2, (70, 60))
    preview._update_canvas_scene(page, layer, (100, 240))
    assert photos.count((100, 100)) == 1  # No base photo recreation on a draft change.
    preview.canvas.winfo_width = lambda: 400
    preview.canvas.winfo_height = lambda: 400
    preview._draw_scene()
    assert preview.canvas.items[1]["image"] == (400, 400)
    assert preview.canvas.items[2]["image"] == (40, 20)
    assert preview.canvas.coords_calls[-1] == (2, (100, 240))
    assert (page.tobytes(), layer.tobytes()) == original


def test_resize_and_draft_updates_reuse_pdf_cache_and_preserve_export(app, tmp_path, monkeypatch):
    preview = open_preview(app, tmp_path)
    settings = dict(
        watermark_enabled="True",
        watermark_text="WATERMARK",
        watermark_color="#FF0000",
        watermark_size=18,
        watermark_position="custom",
        watermark_x_ratio=0.2,
        watermark_y_ratio=0.7,
    )

    def exported_words():
        with Document(app.input_file_full_path) as doc:
            watermark_pdf_page(doc[0], settings)
            return doc[0].get_text("words")

    before = exported_words()
    snapshot = dict(preview.last_watermark_data)
    cached = preview._cached_page_image

    def fail_open(*args):
        pytest.fail("Resize or same-page draft update reopened the PDF")

    with monkeypatch.context() as patch:
        patch.setattr("src.gui.preview.Document", fail_open)
        for fit in (False, True, False, True):
            preview.fit_var.set(fit)
            preview.window.geometry("640x480")
            app.root.update_idletasks()
            preview._draw_scene()
            preview.preview_watermark(**snapshot, preview_page=1, force_open=False)
        assert preview._cached_page_image is cached
    assert preview.last_watermark_data == snapshot
    assert exported_words() == before


def test_language_theme_and_close_preserve_view_then_cancel_resize(app, tmp_path):
    preview = open_preview(app, tmp_path)
    preview.change_page(1)
    preview.fit_var.set(False)
    window = preview.window
    app.on_language_change("de")
    app._on_theme_mode_change("dark")
    assert preview.window is window
    assert preview.current_page == 2
    assert preview.page_label.cget("text") == "Seite 2 von 3"
    assert not preview.fit_var.get()
    preview._schedule_resize()
    timer = preview._resize_timer
    preview.close()
    assert timer not in app.root.tk.call("after", "info")
    assert preview._scene is None


@pytest.mark.parametrize("orient", ["horizontal", "vertical"])
def test_preview_scrollbars_ignore_empty_action_and_keep_scrolling(app, tmp_path, monkeypatch, orient):
    errors = []
    monkeypatch.setattr(app.root, "report_callback_exception", lambda *args: errors.append(args))
    preview = open_preview(app, tmp_path, sizes=((1400, 1600),))
    preview.fit_var.set(False)
    preview.window.geometry("640x480")
    app.root.update()
    preview._draw_scene()
    scrollbar = next(child for child in preview.canvas.master.winfo_children()
                     if child.winfo_class() == "TScrollbar" and str(child.cget("orient")) == orient)
    command = scrollbar.cget("command")
    view = preview.canvas.xview if orient == "horizontal" else preview.canvas.yview
    before = view()
    app.root.tk.call(command, "")
    assert view() == before
    app.root.tk.call(command, "moveto", "0.5")
    moved = view()[0]
    assert moved > 0
    app.root.tk.call(command, "scroll", "-1", "units")
    assert view()[0] < moved
    assert errors == []
