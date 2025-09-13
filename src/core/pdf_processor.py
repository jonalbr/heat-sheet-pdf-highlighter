"""
PDF processing and highlighting functionality
"""

import json
import re
from typing import List

from pymupdf import Page, Rect, utils

from ..models import HighlightMode


def get_line_bbox(page: Page, match_rect: Rect):
    """
    Get the bounding box of the line containing the given match rectangle.

    Args:
        page (Page): The PDF page object.
        match_rect (Rect): The rectangle representing the match.

    Returns:
        Rect: The bounding box of the line containing the match rectangle.
    """
    words = utils.get_text(page, "words")
    line_rect = Rect(match_rect)
    match_height = match_rect.y1 - match_rect.y0
    threshold = match_height * 0.5

    for word in words:
        word_rect = Rect(word[:4])
        if abs(word_rect.y0 - match_rect.y0) <= threshold and abs(word_rect.y1 - match_rect.y1) <= threshold:
            line_rect = line_rect | word_rect

    return line_rect


def highlight_matching_data(
    page: Page,
    search_str: str,
    only_relevant: bool = True,
    filter_enabled: bool = False,
    names: List[str] = [],
    highlight_mode: HighlightMode = HighlightMode.NAMES_DIFF_COLOR,
):
    """
    Highlights the matching data on a given page based on the search string.

    Args:
        page (Page): The page object on which to highlight the data.
        search_str (str): The string to search for and highlight.
        only_relevant (bool, optional): If True, only highlights the data if it matches the relevant line pattern.
            Defaults to False.
        filter_enabled (bool, optional): If True, enables filtering based on the relevant line pattern.
            Defaults to False.
        names (List[str], optional): A list of names to filter the data. Only lines containing any of these names will be highlighted.
            Defaults to an empty list.
        highlight_mode (HighlightMode, optional): The highlight mode to use. Can be one of the values from the HighlightMode enum.
            Defaults to HighlightMode.NAMES_DIFF_COLOR.

    Returns:
        Tuple[int, int]: A tuple containing the number of matches found and the number of matches skipped.

    """
    matches_found = 0
    skipped_matches = 0
    text_instances = utils.search_for(page, search_str)

    # Adjusted regex to consider new lines between elements of the pattern
    relevant_line_pattern = re.compile(
        r"(?i)(?:Bahn\s)?\d+\s.*?\s" + re.escape(search_str) + r"\s.*?(?:\d{1,2}[:.,;]\d{2}(?:,|\.)\d{2}|\d{1,2}[:.,;]\d{2}|NT|ohne)",
        re.DOTALL,  # Allows for matching across multiple lines
    )
    names_pattern = re.compile(r"\b(?:{})\b".format("|".join([re.escape(name) for name in names])), re.IGNORECASE)

    for inst in text_instances:
        # Increment matches found
        matches_found += 1
        line_rect = get_line_bbox(page, inst)  # Get the bounding box for the entire line
        if only_relevant:
            # Find the line of text that contains the instance
            line_text = utils.get_text(page, "text", clip=line_rect)  # Extract text within this rectangle

            # Ensure line_text is a string for regex search
            if isinstance(line_text, list):
                line_text = " ".join(str(item) for item in line_text)
            elif isinstance(line_text, dict):
                line_text = json.dumps(line_text)
            elif not isinstance(line_text, str):
                line_text = str(line_text)

            # Check if the extracted line matches the relevant line pattern
            if not re.search(relevant_line_pattern, line_text):
                skipped_matches += 1
                continue  # Skip highlighting if the line does not match the pattern

            if highlight_mode == HighlightMode.ONLY_NAMES and not names_pattern.search(line_text) and filter_enabled:
                skipped_matches += 1
                continue  # Skip highlighting if the line does not contain any of the names

            highlight = page.add_highlight_annot(line_rect)

            if highlight_mode == HighlightMode.NAMES_DIFF_COLOR and names_pattern.search(line_text) and filter_enabled:
                # light highlight blue
                highlight.set_colors(stroke=[196 / 255, 250 / 255, 248 / 255])
                highlight.update()
            else:
                highlight.set_colors(stroke=[255 / 255, 255 / 255, 166 / 255])
                highlight.update()
        else:
            # Highlight the line if only_relevant is False
            highlight = page.add_highlight_annot(line_rect)
            highlight.update()

    return matches_found, skipped_matches
