from types import SimpleNamespace

from src.gui import main_window
from src.gui.main_window import PDFHighlighterApp
from src.gui.ui_strings import plural_strings


class Var:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class Root:
    def after_idle(self, callback):
        callback()


def make_processing_app():
    app = PDFHighlighterApp.__new__(PDFHighlighterApp)
    app.root = Root()
    app.paths = SimpleNamespace(is_valid_path=lambda path: None)
    app.app_settings = SimpleNamespace(settings={"ocr_enabled": "False"})
    app.search_phrase_var = Var("CLUB")
    app.relevant_lines_var = Var(1)
    app.enable_filter_var = Var(0)
    app.highlight_mode_var = Var("ONLY_NAMES")
    app.names_var = Var("Alice, Bob")
    app.ocr_detection_path = None
    app.ocr_detection_result = None
    app.strings = {"status_saving": "Saving", "status_done": "Done"}
    app.plural_strings = plural_strings
    app.n_ = lambda singular, plural, count: singular if count == 1 else plural
    app._ask_output_file_threadsafe = lambda input_file: "out.pdf"
    app.start_indeterminate_progress = lambda status: setattr(app, "saving_status", status)
    app.update_progress = lambda *args: app.progress_updates.append(args)
    app.finalize_processing = lambda: setattr(app, "finalized", True)
    app.progress_updates = []
    app.finalized = False
    return app


def test_process_pdf_highlights_watermarks_saves_and_reports(monkeypatch):
    app = make_processing_app()
    pages = [object(), object()]
    documents = []
    highlights = []
    watermarks = []
    saved = []
    infos = []

    class FakeDocument:
        def __init__(self, path):
            self.path = path
            self.is_encrypted = False
            self.closed = False
            documents.append(self)

        def __len__(self):
            return len(pages)

        def __getitem__(self, index):
            return pages[index]

        def close(self):
            self.closed = True

    def fake_highlight(**kwargs):
        highlights.append(kwargs)
        return 1, 0

    monkeypatch.setattr(main_window, "Document", FakeDocument)
    monkeypatch.setattr(main_window, "highlight_matching_data", fake_highlight)
    monkeypatch.setattr(main_window, "watermark_pdf_page", lambda page, settings: watermarks.append((page, settings)))
    monkeypatch.setattr(main_window, "save_compact_pdf", lambda document, output_file: saved.append((document, output_file)))
    monkeypatch.setattr(main_window, "show_info", lambda app, title, message: infos.append((title, message)))

    app.process_pdf("input.pdf")

    assert [call["page"] for call in highlights] == pages
    assert [call["search_str"] for call in highlights] == ["CLUB", "CLUB"]
    assert watermarks == [(page, app.app_settings.settings) for page in pages]
    assert app.progress_updates == [(1, 2, 1, 0), (2, 2, 2, 0)]
    assert saved == [(documents[0], "out.pdf")]
    assert documents[0].closed
    assert infos == [("Done", "Processing complete: 2 matches found. 0 skipped.")]
    assert app.saving_status == "Saving"
    assert app.finalized
