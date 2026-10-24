"""Behavioral checks for independent selection, colors and annotation density."""

import pymupdf
import pytest

from src.core.pdf_processor import highlight_matching_data, parse_names
from src.models import MarkingMode


@pytest.mark.parametrize("relevant", [True, False])
@pytest.mark.parametrize(
    "mode, expected",
    [
        (MarkingMode.ALL_MATCHES, ["#112233", "#112233"]),
        (MarkingMode.DISTINGUISH_NAMES, ["#AABBCC", "#112233"]),
        (MarkingMode.NAMED_MATCHES, ["#AABBCC"]),
    ],
)
def test_mode_color_matrix_and_save_reload(tmp_path, relevant, mode, expected):
    path = tmp_path / "marked.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 60), "Bahn 1 Alice CLUB 00:59.99")
        page.insert_text((50, 90), "Bahn 2 Bob CLUB 01:10.00")
        found, skipped = highlight_matching_data(
            page,
            "CLUB",
            relevant,
            ("Alice",),
            mode,
            "#112233",
            "#AABBCC",
        )
        assert found == 2
        assert skipped == (1 if mode == MarkingMode.NAMED_MATCHES else 0)
        document.save(path)
    with pymupdf.open(path) as document:
        page = document[0]
        annotations = list(page.annots())
        assert len(annotations) == len(expected)
        for annotation, color in zip(annotations, expected):
            assert annotation.colors["stroke"] == pytest.approx([int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)])
            assert annotation.blendmode == "Multiply"
            assert annotation.flags & pymupdf.PDF_ANNOT_IS_PRINT


def test_duplicate_occurrences_do_not_darken_or_remove_existing_annotations():
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 60), "Bahn 1 CLUB Alice CLUB 00:59.99")
        existing = page.add_text_annot((400, 50), "Keep me")
        existing_xref = existing.xref
        assert highlight_matching_data(page, "CLUB") == (2, 0)
        annotations = list(page.annots())
        assert len(annotations) == 2
        assert annotations[0].xref == existing_xref
        # White background under a single pastel-yellow highlight, away from glyphs.
        assert page.get_pixmap().pixel(80, 50) == (255, 255, 166)


@pytest.mark.parametrize("names", [(), ("", " ")])
def test_empty_names_never_match_every_line(names):
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 60), "Bahn 1 Alice CLUB 00:59.99")
        assert highlight_matching_data(page, "CLUB", names=names, marking_mode=MarkingMode.NAMED_MATCHES) == (1, 1)
        assert list(page.annots()) == []


def test_names_are_split_consistently():
    assert parse_names(" Alice,\n Bob ,,\nClara ") == ("Alice", "Bob", "Clara")
