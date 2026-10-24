"""
PDF processing and highlighting functionality
"""

import json
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from pymupdf import Page, Rect

from ..constants import HIGHLIGHT_COLOR_DEFAULT, HIGHLIGHT_COLOR_NAMES
from ..models import MarkingMode


def get_line_bbox(page: Page, match_rect: Rect, *, words=None):
    """
    Get the bounding box of the line containing the given match rectangle.

    Args:
        page (Page): The PDF page object.
        match_rect (Rect): The rectangle representing the match.

    Returns:
        Rect: The bounding box of the line containing the match rectangle.
    """
    words = page.get_text("words") if words is None else words
    line_rect = Rect(match_rect)
    match_height = match_rect.y1 - match_rect.y0
    threshold = match_height * 0.5

    for word in words:
        word_rect = Rect(word[:4])
        if abs(word_rect.y0 - match_rect.y0) <= threshold and abs(word_rect.y1 - match_rect.y1) <= threshold:
            line_rect = line_rect | word_rect

    return line_rect


@dataclass(frozen=True)
class LineMatch:
    """A search occurrence classified independently of its marking style."""

    rect: Rect
    relevant: bool
    named: bool


def parse_names(value: str) -> tuple[str, ...]:
    """Read comma- or newline-separated names, ignoring empty entries."""
    return tuple(name.strip() for name in re.split(r"[,\n]+", value) if name.strip())


def matching_patterns(search_str: str, names: Sequence[str] = ()):
    """Compile the shared export/preview relevance and name rules."""
    relevant = re.compile(
        r"(?i)(?:Bahn\s)?\d+\s.*?\s" + re.escape(search_str)
        + r"\s.*?(?:\d{1,2}[:.,;]\d{2}(?:,|\.)\d{2}|\d{1,2}[:.,;]\d{2}|NT|ohne)",
        re.DOTALL,
    )
    clean_names = [name.strip() for name in names if name.strip()]
    named = re.compile(r"\b(?:{})\b".format("|".join(re.escape(name) for name in clean_names)), re.IGNORECASE) if clean_names else None
    return relevant, named


def add_line_highlight(page, match, marking_mode, highlight_color_default, highlight_color_names):
    """Apply the same annotation appearance to a validated export or preview line."""
    color = highlight_color_names if marking_mode != MarkingMode.ALL_MATCHES and match.named else highlight_color_default
    annotation = page.add_highlight_annot(match.rect)
    annotation.set_colors(stroke=[int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)])
    annotation.update(blend_mode="Multiply", opacity=1)


def iter_matching_lines(page: Page, search_str: str, names: Sequence[str] = ()) -> Iterator[LineMatch]:
    """Classify every search occurrence using the same rules for preview and output."""
    if not search_str.strip():
        return
    relevant_pattern, names_pattern = matching_patterns(search_str, names)
    for instance in page.search_for(search_str):
        rect = get_line_bbox(page, instance)
        text = page.get_text("text", clip=rect)
        if isinstance(text, list):
            text = " ".join(str(item) for item in text)
        elif isinstance(text, dict):
            text = json.dumps(text)
        elif not isinstance(text, str):
            text = str(text)
        yield LineMatch(rect, bool(relevant_pattern.search(text)), bool(names_pattern and names_pattern.search(text)))


def highlight_matching_data(
    page: Page,
    search_str: str,
    only_relevant: bool = True,
    names: Sequence[str] = (),
    marking_mode: MarkingMode = MarkingMode.ALL_MATCHES,
    highlight_color_default: str = HIGHLIGHT_COLOR_DEFAULT,
    highlight_color_names: str = HIGHLIGHT_COLOR_NAMES,
) -> tuple[int, int]:
    """Mark eligible lines once, counting found/skipped search occurrences.

    Relevance and name selection apply independently. Existing source annotations
    are preserved; only duplicate rectangles generated in this call are suppressed.
    """
    matches_found = skipped_matches = 0
    marked = set()
    for match in iter_matching_lines(page, search_str, names):
        matches_found += 1
        if (only_relevant and not match.relevant) or (marking_mode == MarkingMode.NAMED_MATCHES and not match.named):
            skipped_matches += 1
            continue
        key = tuple(round(coordinate, 2) for coordinate in match.rect)
        if key in marked:
            continue
        marked.add(key)
        add_line_highlight(page, match, marking_mode, highlight_color_default, highlight_color_names)
    return matches_found, skipped_matches
