from types import SimpleNamespace

import pytest

from src import app as app_module


class Var:
    def set(self, value):
        self.value = value


class Root:
    def __init__(self):
        self.calls = []
        self.destroyed = False
        self.mainloop_called = False
        self.positions = []

    def update_idletasks(self):
        self.calls.append("idle")

    def update(self):
        self.calls.append("update")

    def geometry(self, value):
        self.positions.append(value)

    def destroy(self):
        self.destroyed = True

    def mainloop(self):
        self.mainloop_called = True


@pytest.mark.parametrize("value, expected", [(None, False), ("yes", False), ("1", True), ("true", True), ("True", True)])
def test_is_screenshot_mode_reads_environment(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("HSPH_SCREENSHOT_MODE", raising=False)
    else:
        monkeypatch.setenv("HSPH_SCREENSHOT_MODE", value)

    assert app_module._is_screenshot_mode() is expected


def test_setup_screenshot_state_populates_transient_settings():
    enable_filter_var = Var()
    names_var = Var()
    settings = {}
    app = SimpleNamespace(
        enable_filter_var=enable_filter_var,
        names_var=names_var,
        app_settings=SimpleNamespace(settings=settings),
    )

    app_module._setup_screenshot_state(app)

    assert enable_filter_var.value == 1
    assert "Klara Sophie Meier" in names_var.value
    assert settings == {
        "watermark_enabled": "True",
        "watermark_text": "SGS Hamburg",
        "watermark_color": "#FF9F14",
        "watermark_size": 20,
    }


@pytest.mark.parametrize(
    ("target", "expected_call"),
    [("filter", "filter"), ("watermark", "watermark"), ("devtools", "devtools")],
)
def test_preview_target_opens_requested_window(target, expected_call):
    calls = []
    app = SimpleNamespace(
        open_filter_window=lambda: calls.append("filter"),
        open_watermark_window=lambda: calls.append("watermark"),
        dev_tools=SimpleNamespace(open=lambda: calls.append("devtools")),
    )

    app_module._preview_target(app, SimpleNamespace(), target, None)

    assert calls == [expected_call]


def test_preview_target_uses_defaults_for_bad_preview_settings(monkeypatch):
    calls = []
    root = SimpleNamespace()
    settings = {
        "watermark_size": "not-a-number",
        "watermark_position": "custom",
        "watermark_x_ratio": 0.25,
        "watermark_y_ratio": 0.75,
    }
    app = SimpleNamespace(
        root=root,
        app_settings=SimpleNamespace(settings=settings),
        preview_watermark=lambda **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr(app_module.os.path, "exists", lambda path: path == "sample.pdf")

    app_module._preview_target(app, root, "preview", "sample.pdf")

    assert app.input_file_full_path == "sample.pdf"
    assert calls == [
        {
            "enabled": 1,
            "text": "Error",
            "color": "#FF9F14",
            "size": 20,
            "position": "custom",
            "x_ratio": 0.25,
            "y_ratio": 0.75,
            "preview_page": 1,
            "origin": root,
            "force_open": True,
        }
    ]


def test_capture_and_save_prepares_target_but_skips_capture_off_windows(monkeypatch):
    root = Root()
    preview_calls = []
    monkeypatch.setenv("HSPH_SCREENSHOT_PDF", "preview.pdf")
    monkeypatch.setattr(app_module.os, "name", "posix")
    monkeypatch.setattr(app_module, "_preview_target", lambda app, root, target, pdf: preview_calls.append((target, pdf)))
    monkeypatch.setattr(
        app_module,
        "_windows_capture",
        lambda *args: (_ for _ in ()).throw(AssertionError("should not capture off Windows")),
    )

    app_module._capture_and_save(root, SimpleNamespace(), "shot.png", "preview", delay=0)

    assert root.calls == ["idle", "update"]
    assert preview_calls == [("preview", "preview.pdf")]


def test_save_capture_image_handles_paths_with_and_without_parent_directory(monkeypatch, tmp_path):
    class FakeImage:
        def save(self, path):
            self.saved_paths.append(path)

    makedirs_calls = []
    image = FakeImage()
    image.saved_paths = []
    monkeypatch.setattr(app_module.os, "makedirs", lambda path, exist_ok: makedirs_calls.append((path, exist_ok)))

    app_module._save_capture_image(image, "shot.png")
    app_module._save_capture_image(image, str(tmp_path / "captures" / "shot.png"))

    assert makedirs_calls == [(str(tmp_path / "captures"), True)]
    assert image.saved_paths == ["shot.png", str(tmp_path / "captures" / "shot.png")]


def test_main_runs_mainloop_without_screenshot(monkeypatch):
    root = Root()
    monkeypatch.delenv("HSPH_SCREENSHOT_MODE", raising=False)
    monkeypatch.delenv("HSPH_SCREENSHOT_PATH", raising=False)
    monkeypatch.setattr(app_module, "Tk", lambda: root)
    monkeypatch.setattr(app_module, "PDFHighlighterApp", lambda root: SimpleNamespace(root=root))

    app_module.main()

    assert root.mainloop_called


def test_main_screenshot_mode_captures_and_destroys_root(monkeypatch):
    root = Root()
    capture_calls = []

    def make_app(root):
        return SimpleNamespace(
            root=root,
            enable_filter_var=Var(),
            names_var=Var(),
            app_settings=SimpleNamespace(settings={}),
        )

    monkeypatch.setenv("HSPH_SCREENSHOT_MODE", "1")
    monkeypatch.setenv("HSPH_SCREENSHOT_PATH", "shot.png")
    monkeypatch.setenv("HSPH_SCREENSHOT_TARGET", "main")
    monkeypatch.setenv("HSPH_SCREENSHOT_DELAY", "0")
    monkeypatch.setattr(app_module, "Tk", lambda: root)
    monkeypatch.setattr(app_module, "PDFHighlighterApp", make_app)
    monkeypatch.setattr(
        app_module,
        "_capture_and_save",
        lambda root, app, path, target, delay: capture_calls.append((path, target, delay)),
    )

    app_module.main()

    assert root.positions == ["+10000+10000"]
    assert capture_calls == [("shot.png", "main", 0.0)]
    assert root.destroyed
    assert not root.mainloop_called
