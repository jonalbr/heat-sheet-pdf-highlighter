"""
OCR helpers for scanned PDF processing.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from io import BytesIO
import logging
import math
import multiprocessing
import os
from pathlib import Path
import queue
import tempfile
import time
import traceback
from typing import Callable, Mapping

from PIL import Image
import pymupdf
from pymupdf import Document, Page, Rect

from ..models import HighlightMode
from .pdf_processor import highlight_matching_data
from .watermark import watermark_pdf_page

OCR_SAMPLE_PAGES = 3
OCR_REDUCED_DPI = 144
OCR_REDUCED_JPEG_QUALITY = 75
OCR_LARGE_MULTIPLIER = 1.5
OCR_LARGE_MIN_INCREASE_BYTES = 0
OCR_PROCESS_POLL_SECONDS = 0.1
OCR_MAX_WORKERS = 8


class OcrCancelled(Exception):
    """Raised when OCR processing is cancelled by the user."""


@dataclass(frozen=True)
class HighlightContext:
    search_str: str
    only_relevant: bool
    filter_enabled: bool
    names: list[str]
    highlight_mode: HighlightMode


@dataclass(frozen=True)
class OcrSaveResult:
    used_reduced_output: bool
    reduction_failed: bool
    output_size: int
    normal_size: int
    reduced_size: int | None = None


def sampled_page_numbers(total_pages: int, sample_pages: int = OCR_SAMPLE_PAGES) -> list[int]:
    """Return stable page indexes for fast OCR need detection."""
    if total_pages <= 0 or sample_pages <= 0:
        return []
    if total_pages <= sample_pages:
        return list(range(total_pages))
    if sample_pages == 1:
        return [0]

    last_page = total_pages - 1
    step = last_page / (sample_pages - 1)
    return sorted({round(index * step) for index in range(sample_pages)})


def document_needs_ocr(document: Document, sample_pages: int = OCR_SAMPLE_PAGES) -> bool:
    """
    Return True when sampled pages have visible non-text content but no native text.

    Blank PDFs should not prompt for OCR, while image-only scanned PDFs should.
    """
    if document.is_encrypted or len(document) == 0:
        return False

    found_visual_content = False
    for page_number in sampled_page_numbers(len(document), sample_pages):
        page = document[page_number]
        if page.get_text("text").strip():
            return False
        if _page_has_visual_content(page):
            found_visual_content = True

    return found_visual_content


def pdf_needs_ocr(pdf_path: str | Path, sample_pages: int = OCR_SAMPLE_PAGES) -> bool:
    """Open a PDF and check whether it appears to need OCR."""
    document = Document(str(pdf_path))
    try:
        return document_needs_ocr(document, sample_pages)
    finally:
        document.close()


def ensure_bundled_tessdata(tessdata_dir: Path, language: str) -> Path:
    """Validate that every requested OCR language exists in the bundled tessdata directory."""
    required = [part for part in language.split("+") if part]
    missing = [lang for lang in required if not (tessdata_dir / f"{lang}.traineddata").is_file()]
    if missing:
        missing_names = ", ".join(f"{lang}.traineddata" for lang in missing)
        raise FileNotFoundError(f"Missing bundled OCR language data: {missing_names}")
    return tessdata_dir


def create_searchable_ocr_pdf(
    source_document: Document,
    *,
    tessdata_dir: Path,
    language: str,
    dpi: int,
    progress_callback: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> Document:
    """Create a searchable PDF by OCRing each source page as a compressed page image."""
    ocr_document = pymupdf.open()
    total_pages = len(source_document)
    tessdata = str(tessdata_dir)

    try:
        for index, page in enumerate(source_document):
            if is_cancelled and is_cancelled():
                raise OcrCancelled()

            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            page_bytes = pixmap.pdfocr_tobytes(compress=True, language=language, tessdata=tessdata)
            page_document = pymupdf.open(stream=page_bytes, filetype="pdf")
            try:
                ocr_document.insert_pdf(page_document)
            finally:
                page_document.close()

            if progress_callback:
                progress_callback(index + 1, total_pages)

        return ocr_document
    except Exception:
        ocr_document.close()
        raise


def create_searchable_ocr_pdf_in_process(
    source_pdf_path: str | Path,
    output_pdf_path: str | Path,
    *,
    tessdata_dir: Path,
    language: str,
    dpi: int,
    progress_callback: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    poll_interval: float = OCR_PROCESS_POLL_SECONDS,
    worker_count: int | None = None,
) -> None:
    """Create a searchable OCR PDF in a separate process and write it to ``output_pdf_path``."""
    output_path = Path(output_pdf_path)
    context = multiprocessing.get_context("spawn")
    messages = context.Queue()
    cancel_event = context.Event()
    process = context.Process(
        target=_ocr_pdf_process_worker,
        args=(str(source_pdf_path), str(output_path), str(tessdata_dir), language, dpi, messages, cancel_event, worker_count),
    )

    try:
        process.start()
        error_message = None
        while process.is_alive():
            error_message = _drain_ocr_process_messages(messages, progress_callback) or error_message
            if is_cancelled and is_cancelled():
                cancel_event.set()
                process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                raise OcrCancelled()
            time.sleep(poll_interval)

        process.join()
        error_message = _drain_ocr_process_messages(messages, progress_callback) or error_message
        if error_message:
            raise RuntimeError(f"OCR process failed:\n{error_message}")
        if process.exitcode not in (0, None):
            raise RuntimeError(f"OCR process exited with code {process.exitcode}.")
        if not output_path.is_file():
            raise RuntimeError("OCR process did not create an output PDF.")
    finally:
        if process.is_alive():
            cancel_event.set()
            process.terminate()
            process.join(timeout=5)
        with suppress(Exception):
            messages.close()
        with suppress(Exception):
            messages.join_thread()


def save_pdf_path_in_process(
    input_pdf_path: str | Path,
    output_pdf_path: str | Path,
    *,
    original_pdf_path: str | Path | None = None,
    ocr_used: bool,
    reduce_large_outputs: bool,
    is_cancelled: Callable[[], bool] | None = None,
    poll_interval: float = OCR_PROCESS_POLL_SECONDS,
) -> OcrSaveResult | None:
    """Compact and save a prepared PDF in a separate process."""
    output_path = Path(output_pdf_path)
    context = multiprocessing.get_context("spawn")
    messages = context.Queue()
    cancel_event = context.Event()
    process = context.Process(
        target=_save_pdf_process_worker,
        args=(str(input_pdf_path), str(output_path), str(original_pdf_path) if original_pdf_path else "", ocr_used, reduce_large_outputs, messages, cancel_event),
    )

    try:
        process.start()
        error_message = None
        result: OcrSaveResult | None = None
        done = False

        while process.is_alive():
            error_message, result, done = _drain_save_process_messages(messages, result, done) or (error_message, result, done)
            if is_cancelled and is_cancelled():
                cancel_event.set()
                process.join(timeout=2)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
                raise OcrCancelled()
            time.sleep(poll_interval)

        process.join()
        error_message, result, done = _drain_save_process_messages(messages, result, done) or (error_message, result, done)
        if error_message:
            raise RuntimeError(f"Save process failed:\n{error_message}")
        if process.exitcode not in (0, None):
            raise RuntimeError(f"Save process exited with code {process.exitcode}.")
        if not done:
            raise RuntimeError("Save process did not report completion.")
        if not output_path.is_file():
            raise RuntimeError("Save process did not create an output PDF.")
        return result
    finally:
        if process.is_alive():
            cancel_event.set()
            process.terminate()
            process.join(timeout=5)
        _close_multiprocessing_queue(messages)


def _ocr_pdf_process_worker(
    source_pdf_path: str,
    output_pdf_path: str,
    tessdata_dir: str,
    language: str,
    dpi: int,
    messages,
    cancel_event,
    worker_count: int | None,
) -> None:
    try:
        ocr_document = create_searchable_ocr_pdf_from_path(
            source_pdf_path,
            tessdata_dir=Path(tessdata_dir),
            language=language,
            dpi=dpi,
            progress_callback=lambda current, total: messages.put(("progress", current, total)),
            is_cancelled=cancel_event.is_set,
            worker_count=worker_count,
        )
        try:
            if cancel_event.is_set():
                raise OcrCancelled()
            save_compact_pdf(ocr_document, output_pdf_path)
        finally:
            ocr_document.close()
        messages.put(("done",))
    except Exception:
        with suppress(Exception):
            messages.put(("error", traceback.format_exc()))


def _save_pdf_process_worker(
    input_pdf_path: str,
    output_pdf_path: str,
    original_pdf_path: str,
    ocr_used: bool,
    reduce_large_outputs: bool,
    messages,
    cancel_event,
) -> None:
    document: Document | None = None
    try:
        document = Document(input_pdf_path)
        if cancel_event.is_set():
            raise OcrCancelled()
        result = None
        if ocr_used:
            result = save_ocr_pdf_with_size_guard(
                document,
                output_path=output_pdf_path,
                original_pdf_path=original_pdf_path,
                highlight_context=HighlightContext(
                    search_str="",
                    only_relevant=False,
                    filter_enabled=False,
                    names=[],
                    highlight_mode=HighlightMode.NAMES_DIFF_COLOR,
                ),
                settings={},
                reduce_large_outputs=reduce_large_outputs,
                is_cancelled=cancel_event.is_set,
            )
        else:
            save_compact_pdf(document, output_pdf_path)
        messages.put(("done", result))
    except Exception:
        with suppress(Exception):
            messages.put(("error", traceback.format_exc()))
    finally:
        if document is not None:
            with suppress(Exception):
                document.close()


def _drain_save_process_messages(messages, result: OcrSaveResult | None, done: bool) -> tuple[str | None, OcrSaveResult | None, bool] | None:
    error_message = None
    saw_message = False
    while True:
        try:
            message = messages.get_nowait()
        except queue.Empty:
            return (error_message, result, done) if saw_message else None

        saw_message = True
        kind = message[0]
        if kind == "done":
            done = True
            result = message[1]
        elif kind == "error":
            error_message = str(message[1])


def create_searchable_ocr_pdf_from_path(
    source_pdf_path: str | Path,
    *,
    tessdata_dir: Path,
    language: str,
    dpi: int,
    progress_callback: Callable[[int, int], None] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    worker_count: int | None = None,
) -> Document:
    """Create a searchable OCR PDF from a path, using page workers when useful."""
    source_path = Path(source_pdf_path)
    source_document = Document(str(source_path))
    try:
        total_pages = len(source_document)
    finally:
        source_document.close()

    workers = resolve_ocr_worker_count(total_pages, worker_count)
    if workers <= 1:
        source_document = Document(str(source_path))
        try:
            return create_searchable_ocr_pdf(
                source_document,
                tessdata_dir=tessdata_dir,
                language=language,
                dpi=dpi,
                progress_callback=progress_callback,
                is_cancelled=is_cancelled,
            )
        finally:
            source_document.close()

    return _create_searchable_ocr_pdf_parallel(
        source_path,
        total_pages=total_pages,
        tessdata_dir=tessdata_dir,
        language=language,
        dpi=dpi,
        worker_count=workers,
        progress_callback=progress_callback,
        is_cancelled=is_cancelled,
    )


def resolve_ocr_worker_count(total_pages: int, requested_worker_count: int | None = None) -> int:
    """Return a conservative OCR worker count for memory-heavy 300 DPI page OCR."""
    if total_pages <= 1:
        return max(0, total_pages)
    if requested_worker_count is not None:
        return max(1, min(int(requested_worker_count), total_pages))

    cpu_count = getattr(os, "process_cpu_count", os.cpu_count)() or 1
    return max(1, min(OCR_MAX_WORKERS, cpu_count, total_pages))


def _create_searchable_ocr_pdf_parallel(
    source_pdf_path: Path,
    *,
    total_pages: int,
    tessdata_dir: Path,
    language: str,
    dpi: int,
    worker_count: int,
    progress_callback: Callable[[int, int], None] | None,
    is_cancelled: Callable[[], bool] | None,
) -> Document:
    context = multiprocessing.get_context("spawn")
    task_queue = context.Queue()
    result_queue = context.Queue()
    cancel_event = context.Event()
    processes = [
        context.Process(
            target=_ocr_page_process_worker,
            args=(str(source_pdf_path), str(tessdata_dir), language, dpi, task_queue, result_queue, cancel_event),
        )
        for _ in range(worker_count)
    ]

    with tempfile.TemporaryDirectory(prefix="hsph-ocr-pages-") as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        page_paths = [temp_dir / f"page-{page_number:06d}.pdf" for page_number in range(total_pages)]

        try:
            for page_number, page_path in enumerate(page_paths):
                task_queue.put((page_number, str(page_path)))
            for _ in processes:
                task_queue.put(None)

            for process in processes:
                process.start()

            completed_pages: set[int] = set()
            while len(completed_pages) < total_pages:
                if is_cancelled and is_cancelled():
                    cancel_event.set()
                    raise OcrCancelled()

                try:
                    message = result_queue.get(timeout=OCR_PROCESS_POLL_SECONDS)
                except queue.Empty:
                    _raise_if_all_workers_stopped_early(processes, completed_pages, total_pages)
                    continue

                kind = message[0]
                if kind == "page":
                    completed_pages.add(int(message[1]))
                    if progress_callback:
                        progress_callback(len(completed_pages), total_pages)
                elif kind == "error":
                    cancel_event.set()
                    raise RuntimeError(str(message[1]))

            for process in processes:
                process.join(timeout=5)
            if any(process.is_alive() for process in processes):
                cancel_event.set()
                _terminate_processes(processes)
                raise RuntimeError("OCR worker did not stop after processing completed.")
            _raise_if_any_worker_failed(processes)

            return _merge_page_pdfs(page_paths)
        except Exception:
            cancel_event.set()
            _terminate_processes(processes)
            raise
        finally:
            _close_multiprocessing_queue(task_queue)
            _close_multiprocessing_queue(result_queue)


def _ocr_page_process_worker(
    source_pdf_path: str,
    tessdata_dir: str,
    language: str,
    dpi: int,
    task_queue,
    result_queue,
    cancel_event,
) -> None:
    source_document: Document | None = None
    try:
        source_document = Document(source_pdf_path)
        while not cancel_event.is_set():
            try:
                task = task_queue.get(timeout=OCR_PROCESS_POLL_SECONDS)
            except queue.Empty:
                continue
            if task is None:
                return

            page_number, output_path = task
            _write_ocr_page_pdf(
                source_document,
                int(page_number),
                Path(output_path),
                tessdata_dir=Path(tessdata_dir),
                language=language,
                dpi=dpi,
            )
            result_queue.put(("page", int(page_number)))
    except Exception:
        cancel_event.set()
        with suppress(Exception):
            result_queue.put(("error", traceback.format_exc()))
    finally:
        if source_document is not None:
            with suppress(Exception):
                source_document.close()


def _write_ocr_page_pdf(
    source_document: Document,
    page_number: int,
    output_path: Path,
    *,
    tessdata_dir: Path,
    language: str,
    dpi: int,
) -> None:
    page = source_document[page_number]
    pixmap = page.get_pixmap(dpi=dpi, alpha=False)
    page_bytes = pixmap.pdfocr_tobytes(compress=True, language=language, tessdata=str(tessdata_dir))
    output_path.write_bytes(page_bytes)


def _merge_page_pdfs(page_paths: list[Path]) -> Document:
    merged_document = pymupdf.open()
    try:
        for page_path in page_paths:
            page_document = pymupdf.open(str(page_path))
            try:
                merged_document.insert_pdf(page_document)
            finally:
                page_document.close()
        return merged_document
    except Exception:
        merged_document.close()
        raise


def _raise_if_all_workers_stopped_early(processes: list[multiprocessing.Process], completed_pages: set[int], total_pages: int) -> None:
    if len(completed_pages) >= total_pages:
        return
    if all(not process.is_alive() for process in processes):
        _raise_if_any_worker_failed(processes)
        raise RuntimeError("OCR workers stopped before all pages were processed.")


def _raise_if_any_worker_failed(processes: list[multiprocessing.Process]) -> None:
    failed = [process.exitcode for process in processes if process.exitcode not in (0, None)]
    if failed:
        raise RuntimeError(f"OCR worker exited with code {failed[0]}.")


def _terminate_processes(processes: list[multiprocessing.Process]) -> None:
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        with suppress(Exception):
            process.join(timeout=5)


def _close_multiprocessing_queue(worker_queue) -> None:
    with suppress(Exception):
        worker_queue.close()
    with suppress(Exception):
        worker_queue.join_thread()


def _drain_ocr_process_messages(messages, progress_callback: Callable[[int, int], None] | None) -> str | None:
    error_message = None
    while True:
        try:
            message = messages.get_nowait()
        except queue.Empty:
            return error_message

        kind = message[0]
        if kind == "progress" and progress_callback:
            progress_callback(int(message[1]), int(message[2]))
        elif kind == "error":
            error_message = str(message[1])


def save_ocr_pdf_with_size_guard(
    document: Document,
    *,
    output_path: str | Path,
    original_pdf_path: str | Path,
    highlight_context: HighlightContext,
    settings: Mapping,
    reduce_large_outputs: bool,
    is_cancelled: Callable[[], bool] | None = None,
) -> OcrSaveResult:
    """Save a final OCR result compactly, optionally downsampling page images."""
    output = Path(output_path)
    original = Path(original_pdf_path)
    normal_temp = _temporary_pdf_path(output)
    reduced_temp: Path | None = None
    reduction_failed = False

    try:
        _raise_if_cancelled(is_cancelled)
        save_compact_pdf(document, normal_temp)
        _raise_if_cancelled(is_cancelled)
        normal_size = normal_temp.stat().st_size
        original_size = original.stat().st_size if original.exists() else 0

        should_reduce = reduce_large_outputs and is_large_ocr_output(original_size, normal_size)
        if should_reduce:
            try:
                _raise_if_cancelled(is_cancelled)
                reduced_temp = _temporary_pdf_path(output)
                reduced_document = Document(str(normal_temp))
                try:
                    reduce_pdf_image_streams(reduced_document, is_cancelled=is_cancelled)
                    _raise_if_cancelled(is_cancelled)
                    save_compact_pdf(reduced_document, reduced_temp)
                    _raise_if_cancelled(is_cancelled)
                finally:
                    reduced_document.close()

                reduced_size = reduced_temp.stat().st_size
                if reduced_size < normal_size:
                    os.replace(reduced_temp, output)
                    reduced_temp = None
                    return OcrSaveResult(
                        used_reduced_output=True,
                        reduction_failed=False,
                        output_size=output.stat().st_size,
                        normal_size=normal_size,
                        reduced_size=reduced_size,
                    )
            except OcrCancelled:
                raise
            except Exception:
                logging.getLogger("ocr").exception("Failed to reduce large OCR output")
                reduction_failed = True

        _raise_if_cancelled(is_cancelled)
        os.replace(normal_temp, output)
        return OcrSaveResult(
            used_reduced_output=False,
            reduction_failed=reduction_failed,
            output_size=output.stat().st_size,
            normal_size=normal_size,
            reduced_size=reduced_temp.stat().st_size if reduced_temp and reduced_temp.exists() else None,
        )
    finally:
        _safe_unlink(normal_temp)
        if reduced_temp is not None:
            _safe_unlink(reduced_temp)


def reduce_pdf_image_streams(
    document: Document,
    *,
    target_dpi: int = OCR_REDUCED_DPI,
    jpeg_quality: int = OCR_REDUCED_JPEG_QUALITY,
    is_cancelled: Callable[[], bool] | None = None,
) -> bool:
    """
    Downsample embedded page images in-place while preserving text and annotations.

    This is intended for OCR outputs where the expensive text layer is already
    positioned correctly. Only image XObjects are replaced; page text,
    highlights, and watermarks remain in their original coordinates.
    """
    targets = _collect_image_reduction_targets(document, target_dpi)
    changed = False

    for xref, target in targets.items():
        _raise_if_cancelled(is_cancelled)
        page = document[target.page_index]
        pixmap = pymupdf.Pixmap(document, xref)
        try:
            if pixmap.width <= target.width and pixmap.height <= target.height:
                continue

            image = _pixmap_to_rgb_image(pixmap)
            image.thumbnail((target.width, target.height), Image.Resampling.LANCZOS)
            if image.width <= 0 or image.height <= 0:
                continue

            buffer = BytesIO()
            image.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
            page.replace_image(xref, stream=buffer.getvalue())
            changed = True
        finally:
            pixmap = None

    return changed


@dataclass(frozen=True)
class _ImageReductionTarget:
    page_index: int
    width: int
    height: int


def _collect_image_reduction_targets(document: Document, target_dpi: int) -> dict[int, _ImageReductionTarget]:
    targets: dict[int, _ImageReductionTarget] = {}
    for page_index, page in enumerate(document):
        for image_info in page.get_images(full=True):
            xref = int(image_info[0])
            if xref <= 0:
                continue
            for rect in page.get_image_rects(xref):
                width = max(1, math.ceil(rect.width * target_dpi / 72))
                height = max(1, math.ceil(rect.height * target_dpi / 72))
                existing = targets.get(xref)
                if existing is None or (width * height) > (existing.width * existing.height):
                    targets[xref] = _ImageReductionTarget(page_index=page_index, width=width, height=height)
    return targets


def _pixmap_to_rgb_image(pixmap: pymupdf.Pixmap) -> Image.Image:
    image = pixmap.pil_image()
    if image.mode == "RGB":
        return image
    if image.mode in {"RGBA", "LA"}:
        background = Image.new("RGB", image.size, "white")
        alpha = image.getchannel("A")
        background.paste(image.convert("RGB"), mask=alpha)
        return background
    return image.convert("RGB")


def save_compact_pdf(document: Document, output_path: str | Path) -> None:
    """Save a PDF with lossless structural cleanup and compact object streams."""
    with suppress(RuntimeError):
        document.subset_fonts()
    document.ez_save(
        str(output_path),
        garbage=4,
        deflate=True,
        deflate_images=True,
        deflate_fonts=True,
        use_objstms=1,
        compression_effort=0,
    )


def _raise_if_cancelled(is_cancelled: Callable[[], bool] | None) -> None:
    if is_cancelled and is_cancelled():
        raise OcrCancelled()


def is_large_ocr_output(
    original_size: int,
    ocr_size: int,
    *,
    multiplier: float = OCR_LARGE_MULTIPLIER,
    min_increase_bytes: int = OCR_LARGE_MIN_INCREASE_BYTES,
) -> bool:
    """Return True when OCR output increased enough to justify a reduced fallback."""
    if original_size <= 0:
        return False
    return ocr_size >= original_size * multiplier and (ocr_size - original_size) >= min_increase_bytes


def build_reduced_searchable_pdf(
    *,
    original_pdf_path: str | Path,
    ocr_document: Document,
    highlight_context: HighlightContext,
    settings: Mapping,
    image_dpi: int = OCR_REDUCED_DPI,
    jpeg_quality: int = OCR_REDUCED_JPEG_QUALITY,
) -> Document:
    """Build a smaller raster PDF with invisible OCR text and fresh highlights."""
    source_document = Document(str(original_pdf_path))
    reduced_document = pymupdf.open()

    try:
        if len(source_document) != len(ocr_document):
            raise ValueError("Source and OCR documents have different page counts.")

        for page_number, source_page in enumerate(source_document):
            output_page = reduced_document.new_page(width=source_page.rect.width, height=source_page.rect.height)
            pixmap = source_page.get_pixmap(dpi=image_dpi, alpha=False)
            output_page.insert_image(output_page.rect, stream=pixmap.tobytes("jpeg", jpg_quality=jpeg_quality))

            _insert_hidden_text_from_page(output_page, ocr_document[page_number])
            highlight_matching_data(
                page=output_page,
                search_str=highlight_context.search_str,
                only_relevant=highlight_context.only_relevant,
                filter_enabled=highlight_context.filter_enabled,
                names=highlight_context.names,
                highlight_mode=highlight_context.highlight_mode,
            )
            watermark_pdf_page(output_page, settings)

        return reduced_document
    except Exception:
        reduced_document.close()
        raise
    finally:
        source_document.close()


def _page_has_visual_content(page: Page) -> bool:
    if page.get_images(full=True):
        return True
    try:
        return bool(page.get_drawings())
    except Exception:
        logging.getLogger("ocr").debug("Failed to inspect page drawings during OCR detection", exc_info=True)
        return False


def _insert_hidden_text_from_page(output_page: Page, ocr_page: Page) -> None:
    for block in ocr_page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            text = "".join(str(span.get("text", "")) for span in spans).strip()
            if not text:
                continue

            bbox = line.get("bbox")
            if not bbox:
                continue
            rect = Rect(bbox)
            if rect.is_empty or rect.is_infinite:
                continue

            span_sizes = [float(span.get("size", 0)) for span in spans if span.get("size")]
            fontsize = min(span_sizes) if span_sizes else max(1.0, min(rect.height * 0.8, 72.0))
            rc = output_page.insert_textbox(rect, text, fontsize=fontsize, render_mode=3, overlay=True)
            if rc < 0:
                output_page.insert_text((rect.x0, rect.y1), text, fontsize=fontsize, render_mode=3, overlay=True)


def _temporary_pdf_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(prefix=f".{target.stem}-", suffix=".pdf", dir=target.parent, delete=False)
    handle.close()
    return Path(handle.name)


def _safe_unlink(path: str | Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass
