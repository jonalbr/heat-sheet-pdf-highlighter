"""Progressive, stable PDF excerpts rendered in one reusable worker process."""

from collections import deque
from dataclasses import dataclass, replace
import logging
import re
import time

import pymupdf

from ..constants import HIGHLIGHT_COLOR_DEFAULT, HIGHLIGHT_COLOR_NAMES
from ..models import MarkingMode
from .pdf_processor import LineMatch, add_line_highlight, highlight_matching_data, matching_patterns
from .preview_index import DocumentIndex

PREVIEW_WIDTH = 560
PREVIEW_HEIGHT = 150
SPLIT_HEIGHT = 55
EXAMPLE_PAGE_SIZE = (520, 130)


@dataclass(frozen=True)
class ExampleText:
    """Translated example text captured by Tk; no gettext in the worker."""

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
    """Plain immutable options for one request."""

    source_path: str | None
    search_str: str
    only_relevant: bool
    names: tuple[str, ...]
    marking_mode: MarkingMode
    example_text: ExampleText
    highlight_color_default: str = HIGHLIGHT_COLOR_DEFAULT
    highlight_color_names: str = HIGHLIGHT_COLOR_NAMES


@dataclass(frozen=True)
class PreviewSnippet:
    """One real excerpt with its original page and demonstrated category."""

    png: bytes
    page_number: int
    role: str = "mixed"


@dataclass(frozen=True)
class PreviewResult:
    """One or two excerpts plus honest progressive-search state."""

    png: bytes
    page_number: int | None
    no_named_matches: bool = False
    snippets: tuple[PreviewSnippet, ...] = ()
    state: str = "ready"
    prepared_pages: int = 0
    total_pages: int = 0


@dataclass(frozen=True)
class Area:
    """An unrotated native-page crop; stable across colours and name additions."""

    page: int
    clip: tuple
    role: str = "mixed"


@dataclass(frozen=True)
class PreviewSource:
    """Clean first-page copy retained for inspection and read-only regression tests."""

    pdf: bytes
    clip: tuple
    page_number: int
    has_named_match: bool
    has_text: bool


class PreviewSearch:
    """An interruptible early-exit search; index hints change order, not correctness."""

    def __init__(self, request, index):
        self.request = request
        self.relevant, self.names = matching_patterns(request.search_str, request.names)
        # Select comparable examples across modes; the mode only changes ink.
        self.mixed = bool(self.names)
        self.pending_pages = deque()
        self.hints = {}
        self.queued = set()
        self.finished = set()
        self.cursors = {}
        self.current = None
        self.named = None
        self.general = None
        self.first_sent = False
        self.done = False
        self.seen = set()
        self.hint_cursor = 0
        self.priority_candidates = None
        self.name_tokens = frozenset(
            match.group().casefold() for name in (request.names if self.mixed else ())
            if (match := re.search(r"\w+", name)) is not None)

    def add_page(self, number, page):
        if number in self.queued:
            return
        self.queued.add(number)
        # Clipped export text can differ from row text, so low-priority pages
        # are never excluded. Native verification will visit them if necessary.
        named = bool(self.mixed and self.name_tokens.intersection(page.tokens))
        term = self.request.search_str.casefold()
        general = any(term in text.casefold() and not (self.names and self.names.search(text))
                      for _, text in page.rows)
        self.hints[number] = named, general
        self.pending_pages.append(number)

    def hint_next(self, index):
        """Classify one prepared page only when the current area was insufficient."""
        if self.priority_candidates is None:
            named_pages = {page for token in self.name_tokens for page in index.token_pages.get(token, ())}
            term_tokens = re.findall(r"\w+", self.request.search_str.casefold())
            term_pages = set(index.token_pages.get(term_tokens[0], ())) if term_tokens else set()
            # This lookup only prioritizes. Substrings, Unicode case variants
            # and dehyphenated matches still receive native fallback verification.
            self.priority_candidates = deque(sorted(named_pages & term_pages)
                + sorted(term_pages - named_pages) + sorted(named_pages - term_pages))
        while self.priority_candidates:
            number = self.priority_candidates.popleft()
            if number not in self.queued:
                self.add_page(number, index.pages[number])
                return
        while self.hint_cursor in self.queued:
            self.hint_cursor += 1
        if self.hint_cursor < len(index.pages):
            self.add_page(self.hint_cursor, index.pages[self.hint_cursor])
            self.hint_cursor += 1

    def priority(self, number):
        if not self.mixed:
            return 0
        needed = 0 if self.named is None else 1
        return 0 if self.hints[number][needed] else 1

    def can_step(self, index):
        available = self.current is not None or self.pending_pages
        priority = (self.current is not None and self.priority(self.current) == 0
                    or any(self.priority(number) == 0 for number in self.pending_pages))
        return not self.done and (index.complete or len(self.queued) < len(index.pages)
                                 or bool(available) and (priority or not self.first_sent))


class MarkingPreviewRenderer:
    """Incremental index, exact candidate verification, and stable selected areas."""

    def __init__(self):
        self.index = DocumentIndex()
        self.selection = ()
        self.query = None
        self._source = None

    def prepare(self, path):
        if self.index.load(path):
            self.selection = ()
            self.query = None
            self._source = None

    def prepare_next(self):
        number = self.index.prepare_next()
        if number is not None and self.query is not None and not self.query.done:
            self.query.add_page(number, self.index.pages[number])
        return number

    def start(self, request):
        """Revalidate only the selected area before looking elsewhere."""
        self.prepare(request.source_path)
        self.query = q = PreviewSearch(request, self.index)
        if not request.search_str.strip() or self.index.total == 0:
            q.done = True
            return self._fallback(q)
        if self.selection:
            for area in self.selection:
                for record in self._visible(area, q):
                    self._remember(q, area.page, record)
            if q.general or q.named:
                q.first_sent = True
                if not q.mixed or (q.named and q.general):
                    q.done = True
                return self._result(q, "ready" if q.done else "searching")
        return None

    def _remember(self, q, page, record):
        if q.request.only_relevant and not record.relevant:
            return
        hit = (page, record)
        if q.names and q.names.search(record.text):
            q.named = q.named or hit
        else:
            q.general = q.general or hit

    def step_query(self):
        """Verify at most one occurrence per step, allowing control messages between steps."""
        q = self.query
        if q is None or q.done:
            return None
        if not self._select_query_page(q):
            if q.done:
                return self._result(q, "single_category" if q.mixed else "ready") if q.first_sent else self._fallback(q)
            return None
        number = q.current
        native = self.index.native(number)
        if number not in q.cursors:
            q.cursors[number] = deque(native.search(q.request.search_str))
        cursor = q.cursors[number]
        if not cursor:
            q.finished.add(number)
            q.current = None
            return None
        instance = cursor.popleft()
        record = native.verify(instance, q.request.search_str, q.relevant)
        key = (number, record.rect)
        if key in q.seen:
            return None
        q.seen.add(key)
        self._remember(q, number, record)
        return self._publish_query_match(q, number, record)

    def _select_query_page(self, q):
        """Select a pending page or yield while hints and preparation catch up."""
        q.hint_next(self.index)
        if (q.first_sent and len(q.queued) < len(self.index.pages)
                and not (q.current is not None and q.priority(q.current) == 0)
                and not any(q.priority(number) == 0 for number in q.pending_pages)):
            return False
        if (q.current is not None and q.priority(q.current) > 0
                and any(q.priority(number) == 0 for number in q.pending_pages)):
            q.pending_pages.appendleft(q.current)
            q.current = None
        if q.current is None:
            if not q.pending_pages:
                if self.index.complete:
                    q.done = True
                return False
            q.current = next((number for number in q.pending_pages if q.priority(number) == 0), q.pending_pages[0])
            q.pending_pages.remove(q.current)
        return True

    def _publish_query_match(self, q, number, record):
        """Publish the first usable excerpt or a completed pair of categories."""
        if q.mixed and q.named and q.general:
            self._prefer_neighbour(q, number, record)
            self.selection = self._areas(q.named, q.general)
            q.first_sent = q.done = True
            return self._result(q, "ready")
        if not q.first_sent and (q.named or q.general):
            page, hit = q.named or q.general
            self.selection = (Area(page, self._clip(page, hit.rect), "named" if q.named else "general"),)
            q.first_sent = True
            q.done = not q.mixed
            return self._result(q, "ready" if q.done else "searching")
        return None

    def _prefer_neighbour(self, q, number, record):
        """Check only nearby hits before choosing split excerpts across distant areas."""
        rect = pymupdf.Rect(record.rect)
        native = self.index.native(number)
        named = bool(q.names and q.names.search(record.text))
        other_page, other = q.general if named else q.named
        if other_page == number and abs(other.rect[1] - rect.y0) <= max(rect.height, 12) * 4:
            return
        nearby = [hit for hit in native.search(q.request.search_str)
                  if abs(hit.y0 - rect.y0) <= max(rect.height, 12) * 4]
        for hit in sorted(nearby, key=lambda hit: abs(hit.y0 - rect.y0))[:8]:
            row = native.verify(hit, q.request.search_str, q.relevant)
            if q.request.only_relevant and not row.relevant:
                continue
            if bool(q.names.search(row.text)) != named:
                if named:
                    q.general = number, row
                else:
                    q.named = number, row
                return

    def _clip(self, number, rect, context_rows=2, line_height=None):
        """Keep whole neighbouring glyphs and constrain padding to inter-row gaps."""
        page = self.index.native(number).page
        rect = pymupdf.Rect(rect)
        height = max(line_height or rect.height, 12)
        context = pymupdf.Rect(rect.x0 - 12, rect.y0 - height * context_rows,
                              rect.x1 + 12, rect.y1 + height * context_rows)
        words = [pymupdf.Rect(w) for w in self.index.native(number).words]
        text_rect = pymupdf.Rect(rect)
        for word in words:
            if word.intersects(context):
                text_rect |= word
        clip = pymupdf.Rect(text_rect.x0 - 12, text_rect.y0 - 4, text_rect.x1 + 12, text_rect.y1 + 4)
        nearby = [word for word in words if word.x1 > clip.x0 and word.x0 < clip.x1]
        above = [word.y1 for word in nearby if word.y1 <= text_rect.y0]
        below = [word.y0 for word in nearby if word.y0 >= text_rect.y1]
        if above:
            clip.y0 = max(clip.y0, (max(above) + text_rect.y0) / 2)
        if below:
            clip.y1 = min(clip.y1, (min(below) + text_rect.y1) / 2)
        clip &= page.rect * page.derotation_matrix
        return tuple(clip)

    def _areas(self, named, general):
        page_a, row_a = named
        page_b, row_b = general
        rect_a, rect_b = pymupdf.Rect(row_a.rect), pymupdf.Rect(row_b.rect)
        if page_a == page_b and abs(rect_a.y0 - rect_b.y0) <= max(rect_a.height, rect_b.height, 12) * 4:
            return (Area(page_a, self._clip(page_a, rect_a | rect_b, line_height=max(rect_a.height, rect_b.height))),)
        return (Area(page_a, self._clip(page_a, rect_a, 0), "named"),
                Area(page_b, self._clip(page_b, rect_b, 0), "general"))

    def _visible(self, area, q):
        native = self.index.native(area.page)
        clip = pymupdf.Rect(area.clip)
        seen = set()
        for instance in native.search(q.request.search_str):
            if not instance.intersects(clip):
                continue
            row = native.verify(instance, q.request.search_str, q.relevant)
            if row.rect not in seen:
                seen.add(row.rect)
                yield row

    def _result(self, q, state):
        snippets = []
        request = q.request
        for area in self.selection:
            native = self.index.native(area.page)
            records = tuple(self._visible(area, q))
            with pymupdf.open(stream=native.clean_pdf(), filetype="pdf") as copy:
                page = copy[0]
                for row in records:
                    named = bool(q.names and q.names.search(row.text))
                    if (request.only_relevant and not row.relevant) or (request.marking_mode == MarkingMode.NAMED_MATCHES and not named):
                        continue
                    add_line_highlight(page, LineMatch(pymupdf.Rect(row.rect), row.relevant, named),
                                       request.marking_mode, request.highlight_color_default, request.highlight_color_names)
                clip = pymupdf.Rect(area.clip) * page.rotation_matrix
                if len(self.selection) == 1:
                    png = self._render_page(page, clip)
                else:
                    png = self._render_page(page, clip, height=SPLIT_HEIGHT)
                snippets.append(PreviewSnippet(png, area.page + 1, area.role))
                if not snippets[:-1]:
                    self._source = PreviewSource(native.clean_pdf(), tuple(clip), area.page + 1, bool(q.named), self.index.has_text)
        missing = bool(q.done and q.mixed and q.named is None and self.index.has_text
                       and request.marking_mode == MarkingMode.NAMED_MATCHES)
        return PreviewResult(snippets[0].png, snippets[0].page_number, missing, tuple(snippets), state,
                             len(self.index.pages), self.index.total)

    def _fallback(self, q):
        state = ("example" if not q.request.search_str.strip() else "unavailable" if self.index.unavailable else "example" if self.index.identity is None
                 else "no_text" if not self.index.has_text else "no_matches")
        result = self._render_example(q.request, no_named_matches=bool(
            q.mixed and self.index.has_text and q.request.marking_mode == MarkingMode.NAMED_MATCHES))
        return replace(result, state=state, prepared_pages=len(self.index.pages), total_pages=self.index.total)

    def render(self, request):
        """Synchronous convenience for tests/tools; the app always uses bounded steps."""
        self.prepare(request.source_path)
        while not self.index.complete:
            self.prepare_next()
        result = self.start(request)
        while not self.query.done:
            result = self.step_query() or result
        return result

    def close(self):
        self.index.close()

    @staticmethod
    def _render_page(page, clip, *, height=PREVIEW_HEIGHT):
        zoom = min(2, PREVIEW_WIDTH / clip.width, height / clip.height)
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False).tobytes("png")

    def _render_example(self, request: PreviewRequest, *, no_named_matches: bool = False) -> PreviewResult:
        """Show a fictional heat with the same columns and spacing as a heat sheet."""
        with pymupdf.open() as document:
            page = document.new_page(width=EXAMPLE_PAGE_SIZE[0], height=EXAMPLE_PAGE_SIZE[1])
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


class _MarkingPreviewWorker:
    """Keep control, rendering and progress state local to the preview process."""

    def __init__(self, connection):
        self.connection = connection
        self.engine = MarkingPreviewRenderer()
        self.document_generation = self.generation = 0
        self.paused = False
        self.request = None
        self.reported = -1
        self.progress_at = 0.0

    def receive_latest(self):
        """Apply only the newest snapshot, stopping immediately on shutdown."""
        latest = None
        while self.connection.poll():
            message = self.connection.recv()
            if message is None:
                return False
            latest = message
        if latest is not None:
            self.sync(latest)
        return True

    def sync(self, message):
        """Update document and request generations before any further PDF work."""
        _, doc_id, path, new_paused, request_id, new_request = message
        changed_doc = doc_id != self.document_generation
        if changed_doc:
            self.engine.prepare(path)
            self.document_generation = doc_id
            self.reported = -1
        if new_paused:
            self.engine.index.pause()
        self.paused = new_paused
        if request_id != self.generation or changed_doc or (self.request is None and new_request is not None):
            self.generation = request_id
            self.request = new_request
            self.engine.query = None
            if self.request is not None and not self.paused:
                self.send_result(self.engine.start(self.request))

    def send_result(self, result):
        """Tag a rendered result with the active document and request."""
        if result is not None:
            self.connection.send(("result", self.document_generation, self.generation, result))

    def step(self):
        """Perform one occurrence or page step, or briefly wait when idle."""
        if self.request is not None and self.engine.query is None:
            self.send_result(self.engine.start(self.request))
        query = self.engine.query
        if query is not None and query.can_step(self.engine.index):
            self.send_result(self.engine.step_query())
        elif not self.engine.index.complete:
            self.engine.prepare_next()
        else:
            self.connection.poll(0.02)

    def report_progress(self):
        """Throttle preparation updates while always reporting completion."""
        now = time.monotonic()
        prepared = len(self.engine.index.pages)
        if prepared != self.reported and (self.engine.index.complete or now - self.progress_at >= 0.05):
            self.connection.send(("progress", self.document_generation, prepared, self.engine.index.total))
            self.reported, self.progress_at = prepared, now


def marking_preview_worker(connection):
    """Serve latest-only control messages between page/occurrence-sized work steps."""
    worker = _MarkingPreviewWorker(connection)
    try:
        connection.send(("ready",))
        while worker.receive_latest():
            if worker.paused:
                connection.poll(0.02)
                continue
            worker.step()
            worker.report_progress()
    except EOFError, BrokenPipeError, OSError:
        pass
    except Exception:
        logging.getLogger(__name__).exception("Preview worker failed")
        try:
            connection.send(("error", worker.document_generation, worker.generation))
        except OSError:
            pass
    finally:
        worker.engine.close()
        connection.close()
