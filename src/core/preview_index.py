"""Incremental, document-local text data and a bounded native page cache."""

from array import array
from collections import OrderedDict
from dataclasses import dataclass
import logging
import re
from pathlib import Path

import pymupdf

from .pdf_processor import get_line_bbox

SEARCH_FLAGS = (pymupdf.TEXT_DEHYPHENATE | pymupdf.TEXT_PRESERVE_WHITESPACE
                | pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_MEDIABOX_CLIP)


def source_identity(path):
    """Fingerprint the source without reading the PDF on the Tk thread."""
    if not path:
        return None
    source = Path(path)
    try:
        stat = source.stat()
        return str(source.resolve()), stat.st_mtime_ns, stat.st_size
    except OSError:
        return str(source.absolute()), None, None


@dataclass(frozen=True)
class IndexedPage:
    """Cheap candidate hints; only native verification establishes matches."""

    text: str
    rows: tuple
    coordinates: array
    tokens: frozenset[str] = frozenset()


@dataclass(frozen=True)
class VerifiedLine:
    """An exact export-rule rectangle and clipped text, independent of names."""

    rect: tuple
    text: str
    relevant: bool


class NativePage:
    """Hot native search state, with exact clipped text cached per search term."""

    def __init__(self, page, indexed):
        # Cache independent page bytes so a warm dialog does not keep the
        # user's original PDF locked after preparation has finished.
        with pymupdf.open() as copy:
            copy.insert_pdf(page.parent, from_page=page.number, to_page=page.number)
            self.pdf = copy.tobytes()
        self.document = pymupdf.open(stream=self.pdf, filetype="pdf")
        self.page = self.document[0]
        self.textpage = self.page.get_textpage(flags=SEARCH_FLAGS)
        coords = indexed.coordinates
        self.words = tuple(tuple(coords[i:i + 4]) for i in range(0, len(coords), 4))
        self.term = None
        self.instances = ()
        self.verified = {}

    def search(self, term):
        if term != self.term:
            self.term = term
            self.instances = tuple(self.page.search_for(term, textpage=self.textpage)) if term.strip() else ()
            self.verified = {}
        return self.instances

    def verify(self, instance, term, relevant_pattern):
        self.search(term)
        rect = get_line_bbox(self.page, instance, words=self.words)
        key = tuple(rect)
        if key not in self.verified:
            # Preserve the export's clipping/whitespace semantics. The row index
            # is only a hint, never a substitute for this authoritative text.
            text = self.page.get_text("text", clip=rect)
            self.verified[key] = VerifiedLine(key, text, bool(relevant_pattern.search(text)))
        return self.verified[key]

    def clean_pdf(self):
        return self.pdf

    def close(self):
        self.document.close()


class DocumentIndex:
    """Prepare one page at a time; retain native objects for at most four pages."""

    def __init__(self):
        self.identity = None
        self.document = None
        self.pages = []
        self.token_pages = {}
        self.total = 0
        self.has_text = False
        self.unavailable = False
        self.hot = OrderedDict()

    @property
    def complete(self):
        return len(self.pages) >= self.total

    def load(self, path):
        identity = source_identity(path)
        if identity == self.identity:
            return False
        self.close()
        self.identity = identity
        self.pages = []
        self.token_pages = {}
        self.total = 0
        self.has_text = False
        self.unavailable = False
        if identity is not None:
            try:
                self.document = pymupdf.open(identity[0])
                if self.document.is_encrypted:
                    raise ValueError("Encrypted preview source")
                self.total = len(self.document)
            except Exception:
                logging.getLogger(__name__).debug("Preview source unavailable", exc_info=True)
                self.close()
                self.unavailable = True
        return True

    def _open(self):
        if self.document is None and self.identity is not None and not self.unavailable:
            self.document = pymupdf.open(self.identity[0])
        return self.document

    def prepare_next(self):
        """Build one small Python row/geometry record, without retaining TextPages."""
        if self.complete:
            return None
        page = self._open()[len(self.pages)]
        textpage = page.get_textpage(flags=pymupdf.TEXTFLAGS_WORDS)
        words = textpage.extractWORDS()
        grouped = []
        for word in sorted(words, key=lambda w: (w[1], w[0])):
            if grouped:
                anchor = grouped[-1][0]
                tolerance = max(word[3] - word[1], anchor[3] - anchor[1]) * 0.5
                same_row = abs(word[1] - anchor[1]) <= tolerance and abs(word[3] - anchor[3]) <= tolerance
            else:
                same_row = False
            if same_row:
                grouped[-1].append(word)
            else:
                grouped.append([word])
        rows = tuple((
            (min(w[0] for w in group), min(w[1] for w in group),
             max(w[2] for w in group), max(w[3] for w in group)),
            " ".join(w[4] for w in sorted(group, key=lambda w: w[0])),
        ) for group in grouped)
        text = textpage.extractText()
        tokens = frozenset(re.findall(r"\w+", text.casefold()))
        indexed = IndexedPage(text, rows, array("f", (c for word in words for c in word[:4])), tokens)
        number = len(self.pages)
        self.pages.append(indexed)
        for token in tokens:
            self.token_pages.setdefault(token, []).append(number)
        self.has_text = self.has_text or bool(words)
        if self.complete:
            self.document.close()
            self.document = None
        return len(self.pages) - 1

    def native(self, number):
        """Obtain one hot page; all PDF objects remain inside the preview worker."""
        if number not in self.hot:
            self.hot[number] = NativePage(self._open()[number], self.pages[number])
            if self.complete:
                self.document.close()
                self.document = None
            if len(self.hot) > 4:
                _, expired = self.hot.popitem(last=False)
                expired.close()
        self.hot.move_to_end(number)
        return self.hot[number]

    def pause(self):
        """Release native file handles while foreground PDF work owns the source."""
        self.close()

    def close(self):
        for native in self.hot.values():
            native.close()
        self.hot.clear()
        if self.document is not None:
            self.document.close()
            self.document = None
