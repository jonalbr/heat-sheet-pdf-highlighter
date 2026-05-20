from io import BytesIO
from pathlib import Path
import queue

from PIL import Image
import pytest

try:
    import pymupdf
except ImportError:  # pragma: no cover
    pymupdf = None

from src.core import ocr
from src.config.paths import Paths
from src.models import HighlightMode

pytestmark = pytest.mark.skipif(pymupdf is None, reason="pymupdf not installed")


def make_text_doc(text: str = "Bahn 1 Alice CLUB 00:59.99"):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), text)
    return doc


def make_image_doc():
    doc = pymupdf.open()
    page = doc.new_page()
    image = Image.new("RGB", (64, 64), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    page.insert_image(pymupdf.Rect(50, 50, 180, 180), stream=buffer.getvalue())
    return doc


def collect_highlights(page):
    highlights = []
    annot = page.first_annot
    while annot:
        highlights.append(annot)
        annot = annot.next
    return highlights


def write_valid_pdf(path: Path, padding: bytes = b""):
    doc = pymupdf.open()
    doc.new_page()
    doc.save(path)
    doc.close()
    if padding:
        with path.open("ab") as file:
            file.write(padding)


def test_save_compact_pdf_uses_lossless_compact_options(tmp_path):
    class FakeDocument:
        def __init__(self):
            self.subset_fonts_called = False
            self.save_args = None
            self.save_kwargs = None

        def subset_fonts(self):
            self.subset_fonts_called = True

        def ez_save(self, *args, **kwargs):
            self.save_args = args
            self.save_kwargs = kwargs

    fake_document = FakeDocument()
    output_path = tmp_path / "compact.pdf"

    ocr.save_compact_pdf(fake_document, output_path)

    assert fake_document.subset_fonts_called is True
    assert fake_document.save_args == (str(output_path),)
    assert fake_document.save_kwargs == {
        "garbage": 4,
        "deflate": True,
        "deflate_images": True,
        "deflate_fonts": True,
        "use_objstms": 1,
        "compression_effort": 0,
    }


def test_sampled_page_numbers_include_first_middle_last():
    assert ocr.sampled_page_numbers(10, 3) == [0, 4, 9]
    assert ocr.sampled_page_numbers(2, 3) == [0, 1]
    assert ocr.sampled_page_numbers(0, 3) == []


def test_resolve_ocr_worker_count_is_capped(monkeypatch):
    monkeypatch.setattr(ocr.os, "cpu_count", lambda: 16)
    if hasattr(ocr.os, "process_cpu_count"):
        monkeypatch.setattr(ocr.os, "process_cpu_count", lambda: 16)

    assert ocr.resolve_ocr_worker_count(0) == 0
    assert ocr.resolve_ocr_worker_count(1) == 1
    assert ocr.resolve_ocr_worker_count(20) == ocr.OCR_MAX_WORKERS
    assert ocr.resolve_ocr_worker_count(20, requested_worker_count=2) == 2
    assert ocr.resolve_ocr_worker_count(2, requested_worker_count=8) == 2


def test_document_with_native_text_does_not_need_ocr():
    doc = make_text_doc()
    try:
        assert not ocr.document_needs_ocr(doc)
    finally:
        doc.close()


def test_image_only_document_needs_ocr():
    doc = make_image_doc()
    try:
        assert ocr.document_needs_ocr(doc)
    finally:
        doc.close()


def test_blank_document_does_not_need_ocr():
    doc = pymupdf.open()
    doc.new_page()
    try:
        assert not ocr.document_needs_ocr(doc)
    finally:
        doc.close()


def test_mixed_sample_with_text_does_not_need_ocr():
    doc = make_image_doc()
    text_page = doc.new_page()
    text_page.insert_text((50, 50), "Bahn 1 Alice CLUB 00:59.99")
    doc.new_page()
    try:
        assert not ocr.document_needs_ocr(doc)
    finally:
        doc.close()


def test_ensure_bundled_tessdata_requires_each_language(tmp_path):
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    (tessdata / "deu.traineddata").write_bytes(b"deu")

    with pytest.raises(FileNotFoundError):
        ocr.ensure_bundled_tessdata(tessdata, "deu+eng")

    (tessdata / "eng.traineddata").write_bytes(b"eng")
    assert ocr.ensure_bundled_tessdata(tessdata, "deu+eng") == tessdata


def test_repository_bundles_german_and_english_tessdata():
    assert (Paths.ocr_tessdata_dir / "deu.traineddata").is_file()
    assert (Paths.ocr_tessdata_dir / "eng.traineddata").is_file()


def test_create_searchable_ocr_pdf_passes_bundled_tessdata(monkeypatch, tmp_path):
    source = make_image_doc()
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    calls = []

    def fake_pdfocr_tobytes(self, *, compress=True, language="eng", tessdata=None):
        calls.append({"compress": compress, "language": language, "tessdata": tessdata})
        doc = make_text_doc()
        try:
            return doc.tobytes()
        finally:
            doc.close()

    monkeypatch.setattr(pymupdf.Pixmap, "pdfocr_tobytes", fake_pdfocr_tobytes)

    try:
        result = ocr.create_searchable_ocr_pdf(source, tessdata_dir=tessdata, language="deu+eng", dpi=300)
        try:
            assert len(result) == 1
            assert result[0].search_for("CLUB")
        finally:
            result.close()
    finally:
        source.close()

    assert calls == [{"compress": True, "language": "deu+eng", "tessdata": str(tessdata)}]


def test_create_searchable_ocr_pdf_from_path_parallel_preserves_page_order(monkeypatch, tmp_path):
    source = pymupdf.open()
    for _ in range(3):
        page = source.new_page()
        image = Image.new("RGB", (32, 32), "white")
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        page.insert_image(pymupdf.Rect(20, 20, 80, 80), stream=buffer.getvalue())
    source_path = tmp_path / "source.pdf"
    source.save(source_path)
    source.close()

    def fake_pdfocr_tobytes(self, *, compress=True, language="eng", tessdata=None):
        doc = make_text_doc(f"Page {len(calls)} CLUB")
        calls.append({"compress": compress, "language": language, "tessdata": tessdata})
        try:
            return doc.tobytes()
        finally:
            doc.close()

    class FakeEvent:
        def __init__(self):
            self.cancelled = False

        def is_set(self):
            return self.cancelled

        def set(self):
            self.cancelled = True

    class FakeProcess:
        def __init__(self, target, args):
            self.target = target
            self.args = args
            self.exitcode = None
            self._alive = False
            self.terminated = False

        def start(self):
            self._alive = True
            self.target(*self.args)
            self.exitcode = 0
            self._alive = False

        def is_alive(self):
            return self._alive

        def join(self, timeout=None):
            pass

        def terminate(self):
            self.terminated = True
            self._alive = False
            self.exitcode = -1

    class FakeQueue:
        def __init__(self):
            self.items = queue.Queue()

        def put(self, item):
            self.items.put(item)

        def get(self, timeout=None):
            try:
                return self.items.get(timeout=timeout or 0)
            except queue.Empty:
                raise

        def close(self):
            pass

        def join_thread(self):
            pass

    class FakeContext:
        def Queue(self):
            return FakeQueue()

        def Event(self):
            return FakeEvent()

        def Process(self, *, target, args):
            return FakeProcess(target, args)

    calls = []
    monkeypatch.setattr(pymupdf.Pixmap, "pdfocr_tobytes", fake_pdfocr_tobytes)
    monkeypatch.setattr(ocr.multiprocessing, "get_context", lambda method: FakeContext())
    progress = []

    result = ocr.create_searchable_ocr_pdf_from_path(
        source_path,
        tessdata_dir=tmp_path,
        language="deu",
        dpi=300,
        progress_callback=lambda current, total: progress.append((current, total)),
        worker_count=2,
    )
    try:
        assert len(result) == 3
        assert [result[index].get_text("text").strip() for index in range(3)] == [
            "Page 0 CLUB",
            "Page 1 CLUB",
            "Page 2 CLUB",
        ]
    finally:
        result.close()

    assert len(calls) == 3
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_create_searchable_ocr_pdf_in_process_reports_progress(monkeypatch, tmp_path):
    output = tmp_path / "ocr.pdf"
    fake_process = None

    class FakeQueue:
        def __init__(self):
            self.messages = [("progress", 1, 3), ("progress", 3, 3), ("done",)]

        def get_nowait(self):
            if not self.messages:
                raise queue.Empty
            return self.messages.pop(0)

        def close(self):
            pass

        def join_thread(self):
            pass

    class FakeProcess:
        exitcode = 0
        daemon = False

        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            output.write_bytes(b"%PDF")

        def is_alive(self):
            return False

        def join(self, timeout=None):
            pass

        def terminate(self):
            pass

    class FakeEvent:
        def set(self):
            pass

        def is_set(self):
            return False

    class FakeContext:
        def Queue(self):
            return FakeQueue()

        def Event(self):
            return FakeEvent()

        def Process(self, *args, **kwargs):
            nonlocal fake_process
            fake_process = FakeProcess(*args, **kwargs)
            return fake_process

    monkeypatch.setattr(ocr.multiprocessing, "get_context", lambda method: FakeContext())
    progress = []

    ocr.create_searchable_ocr_pdf_in_process(
        tmp_path / "source.pdf",
        output,
        tessdata_dir=tmp_path,
        language="deu",
        dpi=300,
        progress_callback=lambda current, total: progress.append((current, total)),
        poll_interval=0,
    )

    assert output.is_file()
    assert progress == [(1, 3), (3, 3)]
    assert fake_process is not None
    assert not fake_process.daemon


def test_create_searchable_ocr_pdf_in_process_surfaces_worker_errors(monkeypatch, tmp_path):
    class FakeQueue:
        def __init__(self):
            self.messages = [("error", "boom")]

        def get_nowait(self):
            if not self.messages:
                raise queue.Empty
            return self.messages.pop(0)

        def close(self):
            pass

        def join_thread(self):
            pass

    class FakeProcess:
        exitcode = 0
        daemon = False

        def start(self):
            pass

        def is_alive(self):
            return False

        def join(self, timeout=None):
            pass

        def terminate(self):
            pass

    class FakeEvent:
        def set(self):
            pass

        def is_set(self):
            return False

    class FakeContext:
        def Queue(self):
            return FakeQueue()

        def Event(self):
            return FakeEvent()

        def Process(self, *args, **kwargs):
            return FakeProcess()

    monkeypatch.setattr(ocr.multiprocessing, "get_context", lambda method: FakeContext())

    with pytest.raises(RuntimeError, match="boom"):
        ocr.create_searchable_ocr_pdf_in_process(
            tmp_path / "source.pdf",
            tmp_path / "ocr.pdf",
            tessdata_dir=tmp_path,
            language="deu",
            dpi=300,
            poll_interval=0,
        )


def test_create_searchable_ocr_pdf_in_process_cancels_worker(monkeypatch, tmp_path):
    fake_process = None

    class FakeQueue:
        def get_nowait(self):
            raise queue.Empty

        def close(self):
            pass

        def join_thread(self):
            pass

    class FakeProcess:
        exitcode = None
        daemon = False

        def __init__(self):
            self.terminated = False

        def start(self):
            pass

        def is_alive(self):
            return not self.terminated

        def join(self, timeout=None):
            pass

        def terminate(self):
            self.terminated = True

    class FakeEvent:
        def __init__(self):
            self.cancelled = False

        def set(self):
            self.cancelled = True

        def is_set(self):
            return self.cancelled

    class FakeContext:
        def Queue(self):
            return FakeQueue()

        def Event(self):
            return FakeEvent()

        def Process(self, *args, **kwargs):
            nonlocal fake_process
            fake_process = FakeProcess()
            return fake_process

    monkeypatch.setattr(ocr.multiprocessing, "get_context", lambda method: FakeContext())

    with pytest.raises(ocr.OcrCancelled):
        ocr.create_searchable_ocr_pdf_in_process(
            tmp_path / "source.pdf",
            tmp_path / "ocr.pdf",
            tessdata_dir=tmp_path,
            language="deu",
            dpi=300,
            is_cancelled=lambda: True,
            poll_interval=0,
        )

    assert fake_process is not None
    assert fake_process.terminated


def test_save_pdf_path_in_process_returns_worker_result(monkeypatch, tmp_path):
    input_pdf = tmp_path / "input.pdf"
    output_pdf = tmp_path / "output.pdf"
    write_valid_pdf(input_pdf)
    expected = ocr.OcrSaveResult(
        used_reduced_output=False,
        reduction_failed=False,
        output_size=123,
        normal_size=123,
    )

    class FakeQueue:
        def __init__(self):
            self.messages = [("done", expected)]

        def get_nowait(self):
            if not self.messages:
                raise queue.Empty
            return self.messages.pop(0)

        def close(self):
            pass

        def join_thread(self):
            pass

    class FakeProcess:
        exitcode = 0

        def start(self):
            output_pdf.write_bytes(b"%PDF")

        def is_alive(self):
            return False

        def join(self, timeout=None):
            pass

        def terminate(self):
            pass

    class FakeEvent:
        def set(self):
            pass

        def is_set(self):
            return False

    class FakeContext:
        def Queue(self):
            return FakeQueue()

        def Event(self):
            return FakeEvent()

        def Process(self, *args, **kwargs):
            return FakeProcess()

    monkeypatch.setattr(ocr.multiprocessing, "get_context", lambda method: FakeContext())

    result = ocr.save_pdf_path_in_process(
        input_pdf,
        output_pdf,
        original_pdf_path=input_pdf,
        ocr_used=True,
        reduce_large_outputs=True,
        poll_interval=0,
    )

    assert result == expected


def test_save_pdf_path_in_process_cancels_worker(monkeypatch, tmp_path):
    input_pdf = tmp_path / "input.pdf"
    output_pdf = tmp_path / "output.pdf"
    write_valid_pdf(input_pdf)
    fake_process = None

    class FakeQueue:
        def get_nowait(self):
            raise queue.Empty

        def close(self):
            pass

        def join_thread(self):
            pass

    class FakeProcess:
        exitcode = None

        def __init__(self):
            self.terminated = False

        def start(self):
            pass

        def is_alive(self):
            return not self.terminated

        def join(self, timeout=None):
            pass

        def terminate(self):
            self.terminated = True

    class FakeEvent:
        def __init__(self):
            self.cancelled = False

        def set(self):
            self.cancelled = True

        def is_set(self):
            return self.cancelled

    class FakeContext:
        def Queue(self):
            return FakeQueue()

        def Event(self):
            return FakeEvent()

        def Process(self, *args, **kwargs):
            nonlocal fake_process
            fake_process = FakeProcess()
            return fake_process

    monkeypatch.setattr(ocr.multiprocessing, "get_context", lambda method: FakeContext())

    with pytest.raises(ocr.OcrCancelled):
        ocr.save_pdf_path_in_process(
            input_pdf,
            output_pdf,
            ocr_used=False,
            reduce_large_outputs=False,
            is_cancelled=lambda: True,
            poll_interval=0,
        )

    assert fake_process is not None
    assert fake_process.terminated


def test_build_reduced_searchable_pdf_keeps_hidden_text_and_highlights(tmp_path):
    source = make_image_doc()
    source_path = tmp_path / "source.pdf"
    source.save(source_path)
    source.close()

    ocr_doc = make_text_doc()
    context = ocr.HighlightContext(
        search_str="CLUB",
        only_relevant=True,
        filter_enabled=False,
        names=[],
        highlight_mode=HighlightMode.NAMES_DIFF_COLOR,
    )

    try:
        reduced = ocr.build_reduced_searchable_pdf(
            original_pdf_path=source_path,
            ocr_document=ocr_doc,
            highlight_context=context,
            settings={"watermark_enabled": "False"},
        )
        try:
            assert reduced[0].search_for("CLUB")
            assert collect_highlights(reduced[0])
        finally:
            reduced.close()
    finally:
        ocr_doc.close()


def test_large_output_detection_requires_ratio_and_minimum_increase():
    assert not ocr.is_large_ocr_output(100, 500, min_increase_bytes=1000)
    assert not ocr.is_large_ocr_output(1000, 1499, min_increase_bytes=0)
    assert ocr.is_large_ocr_output(1000, 1500, min_increase_bytes=0)
    assert ocr.is_large_ocr_output(1000, 2000, min_increase_bytes=100)


def test_reduce_pdf_image_streams_preserves_text_and_highlights():
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    image = Image.new("RGB", (800, 800), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    page.insert_image(pymupdf.Rect(0, 0, 200, 200), stream=buffer.getvalue())
    page.insert_text((30, 50), "Bahn 1 Alice CLUB 00:59.99")
    page.add_highlight_annot(page.search_for("CLUB"))
    before = page.get_images(full=True)[0][2:4]

    try:
        changed = ocr.reduce_pdf_image_streams(doc, target_dpi=72, jpeg_quality=75)

        after = doc[0].get_images(full=True)[0][2:4]
        assert changed
        assert after[0] < before[0]
        assert after[1] < before[1]
        assert doc[0].search_for("CLUB")
        assert collect_highlights(doc[0])
    finally:
        doc.close()


def test_save_size_guard_writes_normal_output_when_not_large(monkeypatch, tmp_path):
    original = tmp_path / "original.pdf"
    output = tmp_path / "output.pdf"
    original.write_bytes(b"original")
    doc = pymupdf.open()

    monkeypatch.setattr(ocr, "save_compact_pdf", lambda _doc, path: Path(path).write_bytes(b"normal"))
    monkeypatch.setattr(ocr, "is_large_ocr_output", lambda *_args, **_kwargs: False)

    result = ocr.save_ocr_pdf_with_size_guard(
        doc,
        output_path=output,
        original_pdf_path=original,
        highlight_context=_context(),
        settings={},
        reduce_large_outputs=True,
    )

    doc.close()
    assert output.read_bytes() == b"normal"
    assert not result.used_reduced_output
    assert not result.reduction_failed


def test_save_size_guard_uses_smaller_reduced_output(monkeypatch, tmp_path):
    original = tmp_path / "original.pdf"
    output = tmp_path / "output.pdf"
    original.write_bytes(b"original")
    doc = pymupdf.open()
    writes = iter([b"x" * 5000, b"small"])

    monkeypatch.setattr(ocr, "save_compact_pdf", lambda _doc, path: write_valid_pdf(Path(path), next(writes)))
    monkeypatch.setattr(ocr, "is_large_ocr_output", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ocr, "reduce_pdf_image_streams", lambda _doc, **_kwargs: True)

    result = ocr.save_ocr_pdf_with_size_guard(
        doc,
        output_path=output,
        original_pdf_path=original,
        highlight_context=_context(),
        settings={},
        reduce_large_outputs=True,
    )

    doc.close()
    assert result.used_reduced_output
    assert not result.reduction_failed
    assert result.reduced_size is not None
    assert result.reduced_size < result.normal_size
    assert output.stat().st_size == result.output_size


def test_save_size_guard_keeps_normal_when_reduced_is_larger(monkeypatch, tmp_path):
    original = tmp_path / "original.pdf"
    output = tmp_path / "output.pdf"
    original.write_bytes(b"original")
    doc = pymupdf.open()
    writes = iter([b"small", b"x" * 5000])

    monkeypatch.setattr(ocr, "save_compact_pdf", lambda _doc, path: write_valid_pdf(Path(path), next(writes)))
    monkeypatch.setattr(ocr, "is_large_ocr_output", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ocr, "reduce_pdf_image_streams", lambda _doc, **_kwargs: True)

    result = ocr.save_ocr_pdf_with_size_guard(
        doc,
        output_path=output,
        original_pdf_path=original,
        highlight_context=_context(),
        settings={},
        reduce_large_outputs=True,
    )

    doc.close()
    assert not result.used_reduced_output
    assert not result.reduction_failed
    assert result.reduced_size is not None
    assert result.reduced_size > result.normal_size
    assert output.stat().st_size == result.output_size


def test_save_size_guard_falls_back_to_normal_when_reduction_fails(monkeypatch, tmp_path):
    original = tmp_path / "original.pdf"
    output = tmp_path / "output.pdf"
    original.write_bytes(b"original")
    doc = pymupdf.open()

    def raise_reduction(_doc):
        raise RuntimeError("boom")

    monkeypatch.setattr(ocr, "save_compact_pdf", lambda _doc, path: write_valid_pdf(Path(path), b"normal"))
    monkeypatch.setattr(ocr, "is_large_ocr_output", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ocr, "reduce_pdf_image_streams", raise_reduction)

    result = ocr.save_ocr_pdf_with_size_guard(
        doc,
        output_path=output,
        original_pdf_path=original,
        highlight_context=_context(),
        settings={},
        reduce_large_outputs=True,
    )

    doc.close()
    assert not result.used_reduced_output
    assert result.reduction_failed
    assert output.stat().st_size == result.output_size


def test_save_size_guard_cleans_temp_files_when_cancelled_after_normal_save(monkeypatch, tmp_path):
    original = tmp_path / "original.pdf"
    output = tmp_path / "output.pdf"
    original.write_bytes(b"original")
    doc = pymupdf.open()
    cancellation_checks = iter([False, True])

    monkeypatch.setattr(ocr, "save_compact_pdf", lambda _doc, path: write_valid_pdf(Path(path), b"normal"))
    monkeypatch.setattr(ocr, "is_large_ocr_output", lambda *_args, **_kwargs: False)

    with pytest.raises(ocr.OcrCancelled):
        ocr.save_ocr_pdf_with_size_guard(
            doc,
            output_path=output,
            original_pdf_path=original,
            highlight_context=_context(),
            settings={},
            reduce_large_outputs=True,
            is_cancelled=lambda: next(cancellation_checks, True),
        )

    doc.close()
    assert not output.exists()
    assert not list(tmp_path.glob(".output-*.pdf"))


def _context():
    return ocr.HighlightContext(
        search_str="CLUB",
        only_relevant=True,
        filter_enabled=False,
        names=[],
        highlight_mode=HighlightMode.NAMES_DIFF_COLOR,
    )
