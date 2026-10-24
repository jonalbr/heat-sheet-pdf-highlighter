"""Read-only PDF excerpts rendered in an isolated, reusable preview process."""

from dataclasses import dataclass
import logging
from pathlib import Path

import pymupdf

from ..constants import HIGHLIGHT_COLOR_DEFAULT, HIGHLIGHT_COLOR_NAMES
from ..models import MarkingMode
from .pdf_processor import highlight_matching_data, iter_matching_lines

PREVIEW_WIDTH = 560
PREVIEW_HEIGHT = 150


@dataclass(frozen=True)
class ExampleText:
    """Already translated example text; workers never import GUI/gettext state."""

    heading: str
    lane: str
    swimmer: str
    year: str
    club: str
    entry_time: str
    lane_format: str
    time_format: str


@dataclass(frozen=True)
class PreviewRequest:
    """Plain data captured by the UI before sending it to the preview worker."""

    source_path: str | None
    search_str: str
    only_relevant: bool
    names: tuple[str, ...]
    marking_mode: MarkingMode
    example_text: ExampleText
    highlight_color_default: str = HIGHLIGHT_COLOR_DEFAULT
    highlight_color_names: str = HIGHLIGHT_COLOR_NAMES


@dataclass(frozen=True)
class PreviewResult:
    """A rendered excerpt and its original one-based page, or an example."""

    png: bytes
    page_number: int | None
    no_named_matches: bool = False


@dataclass(frozen=True)
class PreviewSource:
    """A cached clean page plus document-wide name-search evidence."""

    pdf: bytes | None
    clip: tuple[float, ...] | None
    page_number: int | None
    has_named_match: bool
    has_text: bool


class MarkingPreviewRenderer:
    """Cache a clean page copy and excerpt independently of marking colors."""

    def __init__(self):
        self._key = None
        self._source = None

    def _source_key(self, request: PreviewRequest):
        if not request.source_path or not request.search_str.strip():
            return None
        try:
            path = Path(request.source_path)
            stat = path.stat()
            return (
                str(path.resolve()),
                stat.st_mtime_ns,
                stat.st_size,
                request.search_str,
                request.only_relevant,
                request.names,
                request.marking_mode != MarkingMode.ALL_MATCHES,
            )
        except OSError:
            return None

    def _select_source(self, request: PreviewRequest):
        """Prefer a named eligible line, falling back to the first general match."""
        with pymupdf.open(request.source_path) as document:
            if document.is_encrypted:
                return None
            candidate = None
            prefer_names = request.marking_mode != MarkingMode.ALL_MATCHES and bool(request.names)
            has_named_match = False
            has_text = False
            for page in document:
                has_text = bool(page.get_text("words")) or has_text
                for match in iter_matching_lines(page, request.search_str, request.names):
                    if request.only_relevant and not match.relevant:
                        continue
                    if candidate is None or (prefer_names and match.named):
                        candidate = (page.number, match.rect)
                    if not prefer_names or match.named:
                        has_named_match = match.named
                        break
                if candidate is not None and (not prefer_names or has_named_match):
                    break
            if candidate is None:
                return PreviewSource(None, None, None, False, has_text)
            page_index, rect = candidate
            page = document[page_index]
            height = max(rect.height, 12)
            context = pymupdf.Rect(rect.x0 - 12, rect.y0 - height * 2, rect.x1 + 12, rect.y1 + height * 2)
            # Include complete neighboring text instead of slicing through glyphs
            # at the excerpt boundary. Test against the fixed context, not its
            # expanding union, so dense rows cannot grow this to a whole page.
            text_rect = pymupdf.Rect(rect)
            words = [pymupdf.Rect(word[:4]) for word in page.get_text("words")]
            for word_rect in words:
                if word_rect.intersects(context):
                    text_rect |= word_rect
            clip = pymupdf.Rect(text_rect.x0 - 12, text_rect.y0 - 4, text_rect.x1 + 12, text_rect.y1 + 4)
            # Dense heat sheets leave less than four points between rows. Limit
            # padding to those gaps rather than exposing part of the next row.
            nearby = [word for word in words if word.x1 > clip.x0 and word.x0 < clip.x1]
            above = [word.y1 for word in nearby if word.y1 <= text_rect.y0]
            below = [word.y0 for word in nearby if word.y0 >= text_rect.y1]
            if above:
                clip.y0 = max(clip.y0, (max(above) + text_rect.y0) / 2)
            if below:
                clip.y1 = min(clip.y1, (min(below) + text_rect.y1) / 2)
            clip &= page.rect * page.derotation_matrix
            clip = clip * page.rotation_matrix
            if clip.is_empty or clip.is_infinite:
                return None
            with pymupdf.open() as copy:
                copy.insert_pdf(document, from_page=page_index, to_page=page_index)
                return PreviewSource(copy.tobytes(), tuple(clip), page_index + 1, has_named_match, has_text)

    def render(self, request: PreviewRequest) -> PreviewResult:
        """Render actual output rules on a fresh copy; never mutate the cached page."""
        key = self._source_key(request)
        if key != self._key:
            self._key, self._source = key, None
            if key is not None:
                try:
                    self._source = self._select_source(request)
                except Exception:
                    logging.getLogger(__name__).debug("Cannot read preview source", exc_info=True)
        no_named_matches = (
            request.marking_mode == MarkingMode.NAMED_MATCHES
            and self._source is not None
            and self._source.has_text
            and not self._source.has_named_match
        )
        if self._source is None or self._source.pdf is None:
            return self._render_example(request, no_named_matches=no_named_matches)
        with pymupdf.open(stream=self._source.pdf, filetype="pdf") as document:
            highlight_matching_data(
                document[0],
                request.search_str,
                request.only_relevant,
                request.names,
                request.marking_mode,
                request.highlight_color_default,
                request.highlight_color_names,
            )
            return PreviewResult(
                self._render_page(document[0], pymupdf.Rect(self._source.clip)),
                self._source.page_number,
                no_named_matches,
            )

    @staticmethod
    def _render_page(page, clip) -> bytes:
        # Fix the viewport size while preserving the original PDF aspect ratio.
        zoom = min(2, PREVIEW_WIDTH / clip.width, PREVIEW_HEIGHT / clip.height)
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False).tobytes("png")

    def _render_example(self, request: PreviewRequest, *, no_named_matches: bool = False) -> PreviewResult:
        """Show a fictional heat with the same columns and spacing as a heat sheet."""
        with pymupdf.open() as document:
            page = document.new_page(width=520, height=130)
            labels = request.example_text
            page.insert_text((12, 18), labels.heading, fontsize=12, fontname="hebo")
            for x, title in ((12, labels.lane), (66, labels.swimmer), (266, labels.year), (304, labels.club)):
                page.insert_text((x, 39), title, fontsize=11, fontname="hebo")
            title = labels.entry_time
            page.insert_text(
                (508 - pymupdf.get_text_length(title, fontname="hebo", fontsize=11), 39),
                title,
                fontsize=11,
                fontname="hebo",
            )
            page.draw_line((12, 43), (508, 43), color=(0.5, 0.5, 0.5), width=0.4)
            for lane, name, year, club, time, y in (
                (1, "Mia Sommer", "2012", "Hamburger SC", ("01", "04", "83"), 59),
                (2, "Leon Wagner", "2011", "SGS Hamburg", ("01", "03", "45"), 78),
                (3, "Paul Richter", "2010", "SGS Hamburg", ("01", "02", "78"), 97),
                (4, "Lukas Fischer", "2012", "SVE Hamburg", ("01", "05", "12"), 116),
            ):
                for x, text in ((12, labels.lane_format.format(lane)), (66, name), (266, year), (304, club)):
                    page.insert_text((x, y), text, fontsize=11)
                time = labels.time_format.format(minutes=time[0], seconds=time[1], hundredths=time[2])
                page.insert_text((508 - pymupdf.get_text_length(time, fontsize=11), y), time, fontsize=11)
            highlight_matching_data(
                page,
                "SGS Hamburg",
                request.only_relevant,
                ("Leon Wagner",),
                request.marking_mode,
                request.highlight_color_default,
                request.highlight_color_names,
            )
            return PreviewResult(self._render_page(page, page.rect), None, no_named_matches)


def marking_preview_worker(connection) -> None:
    """Top-level spawn-safe worker; all PDF objects remain inside this process."""
    renderer = MarkingPreviewRenderer()
    try:
        while True:
            message = connection.recv()
            if message is None:
                break
            generation, request = message
            try:
                result = renderer.render(request)
            except Exception:
                logging.getLogger(__name__).exception("Cannot render marking preview")
                result = renderer._render_example(request)
            connection.send((generation, result))
    except EOFError, BrokenPipeError, OSError:
        pass
    finally:
        connection.close()
