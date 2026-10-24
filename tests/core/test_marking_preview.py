"""Preview source selection, faithful rendering and read-only caching."""

from dataclasses import replace
from io import BytesIO

from PIL import Image
import pymupdf
import pytest

from src.core.marking_preview import ExampleText, MarkingPreviewRenderer, PreviewRequest
from src.gui.ui_strings import build_strings
from src.models import MarkingMode
from src.utils.localization import setup_translation


def request(path=None, mode=MarkingMode.DISTINGUISH_NAMES, names=("Alice",), language="en"):
    gettext, _ = setup_translation(language)
    strings = build_strings(gettext)
    example = ExampleText(
        strings["flt_example_heat"].format(1, 3, "10:30"),
        *(
            strings[f"flt_example_{key}"]
            for key in (
                "lane",
                "swimmer",
                "year",
                "club",
                "entry_time",
                "lane_format",
                "time_format",
            )
        ),
    )
    return PreviewRequest(str(path) if path else None, "CLUB", True, names, mode, example)


def make_pdf(path):
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 60), "Not a relevant CLUB row")
        page.insert_text((50, 90), "Bahn 2 Bob CLUB 01:10.00")
        page = document.new_page()
        page.insert_text((50, 60), "Bahn 1 Alice CLUB 00:59.99")
        page.insert_text((50, 85), "Bahn 2 Bob CLUB 01:10.00")
        document.save(path)


def test_prefers_name_and_caches_selection_across_color_changes(tmp_path, monkeypatch):
    path = tmp_path / "input.pdf"
    make_pdf(path)
    original = path.read_bytes()
    renderer = MarkingPreviewRenderer()
    result = renderer.render(request(path))
    assert result.page_number == 2
    monkeypatch.setattr(renderer, "_select_source", lambda request: pytest.fail("Color change searched again"))
    changed = renderer.render(replace(request(path), highlight_color_names="#FFAAAA"))
    assert changed.page_number == 2
    assert changed.png != result.png
    assert path.read_bytes() == original


def test_general_fallback_respects_relevance_and_mode(tmp_path):
    path = tmp_path / "input.pdf"
    make_pdf(path)
    renderer = MarkingPreviewRenderer()
    result = renderer.render(request(path, MarkingMode.NAMED_MATCHES, ()))
    assert result.page_number == 1
    # The first general eligible line is cached, but no invented name highlight appears.
    with Image.open(BytesIO(result.png)) as image:
        assert all(r == g == b for r, g, b in image.convert("RGB").get_flattened_data())
    irrelevant_allowed = renderer.render(replace(request(path, MarkingMode.ALL_MATCHES), only_relevant=False))
    assert irrelevant_allowed.page_number == 1


@pytest.mark.parametrize("source", [None, "missing.pdf"])
def test_example_without_source_or_names(source):
    renderer = MarkingPreviewRenderer()
    result = renderer.render(request(source, MarkingMode.NAMED_MATCHES, ()))
    assert result.page_number is None
    with Image.open(BytesIO(result.png)) as image:
        assert image.width > 100 and image.height > 50


def test_no_matches_scanned_and_unreadable_sources_fall_back(tmp_path):
    path = tmp_path / "input.pdf"
    with pymupdf.open() as document:
        document.new_page()
        document.save(path)
    renderer = MarkingPreviewRenderer()
    assert renderer.render(request(path)).page_number is None
    path.write_bytes(b"not a PDF")
    assert renderer.render(request(path)).page_number is None


def test_changed_file_invalidates_cached_excerpt(tmp_path):
    path = tmp_path / "input.pdf"
    make_pdf(path)
    renderer = MarkingPreviewRenderer()
    assert renderer.render(request(path)).page_number == 2
    path.write_bytes(b"changed source")
    assert renderer.render(request(path)).page_number is None


def test_excerpt_includes_complete_neighboring_glyphs_and_rotated_pages(tmp_path):
    path = tmp_path / "rotated.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        for y, text in (
            (50, "Bahn 1 Alice CLUB 00:59.99"),
            (70, "Bahn 2 Bob CLUB 01:10.00"),
            (90, "Bahn 3 Clara OTHER 01:12.00"),
        ):
            page.insert_text((50, y), text)
        page.set_rotation(90)
        document.save(path)
    renderer = MarkingPreviewRenderer()
    result = renderer.render(request(path))
    assert result.page_number == 1
    source = renderer._source
    with pymupdf.open(stream=source.pdf, filetype="pdf") as document:
        page = document[0]
        clip = pymupdf.Rect(source.clip) * page.derotation_matrix
        assert all(clip.contains(pymupdf.Rect(word[:4])) for word in page.get_text("words"))


def test_dense_heat_excerpt_padding_does_not_cut_into_outside_rows(tmp_path):
    path = tmp_path / "dense.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        for lane in range(1, 9):
            name = "Alice" if lane == 4 else "Bob"
            page.insert_text((50, 50 + lane * 12), f"Bahn {lane} {name} CLUB 01:10.00", fontsize=8)
        document.save(path)
    renderer = MarkingPreviewRenderer()
    renderer.render(request(path))
    source = renderer._source
    with pymupdf.open(stream=source.pdf, filetype="pdf") as document:
        clip = pymupdf.Rect(source.clip)
        for word in document[0].get_text("words"):
            rect = pymupdf.Rect(word[:4])
            assert clip.contains(rect) or not clip.intersects(rect)


@pytest.mark.parametrize("mode", list(MarkingMode))
def test_missing_name_note_only_for_names_only_mode(tmp_path, mode):
    path = tmp_path / "input.pdf"
    make_pdf(path)
    renderer = MarkingPreviewRenderer()
    result = renderer.render(request(path, mode, ("Not In This PDF",)))
    assert result.no_named_matches == (mode == MarkingMode.NAMED_MATCHES)
    assert result.page_number == 1


def test_name_note_clears_with_matching_names_and_uses_cached_search_evidence(tmp_path, monkeypatch):
    path = tmp_path / "input.pdf"
    make_pdf(path)
    renderer = MarkingPreviewRenderer()
    missing = request(path, MarkingMode.DISTINGUISH_NAMES, ("Missing",))
    assert not renderer.render(missing).no_named_matches
    with monkeypatch.context() as patch:
        patch.setattr(renderer, "_select_source", lambda request: pytest.fail("Repeated the same name search"))
        assert renderer.render(replace(missing, marking_mode=MarkingMode.NAMED_MATCHES)).no_named_matches
    assert not renderer.render(request(path, MarkingMode.NAMED_MATCHES)).no_named_matches


def test_name_note_respects_relevance(tmp_path):
    path = tmp_path / "input.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 60), "Alice CLUB without a lane or time")
        page.insert_text((50, 90), "Bahn 2 Bob CLUB 01:10.00")
        document.save(path)
    renderer = MarkingPreviewRenderer()
    current = request(path, MarkingMode.NAMED_MATCHES)
    assert renderer.render(current).no_named_matches
    assert not renderer.render(replace(current, only_relevant=False)).no_named_matches


def test_no_name_note_without_readable_pdf_or_for_synthetic_names(tmp_path):
    renderer = MarkingPreviewRenderer()
    assert not renderer.render(request(mode=MarkingMode.NAMED_MATCHES)).no_named_matches
    path = tmp_path / "input.pdf"
    path.write_bytes(b"Unreadable PDF")
    assert not renderer.render(request(path, MarkingMode.NAMED_MATCHES)).no_named_matches
    with pymupdf.open() as document:
        document.new_page()
        document.save(path)
    assert not renderer.render(request(path, MarkingMode.NAMED_MATCHES)).no_named_matches


def test_missing_names_when_no_search_hits_shows_note_with_example(tmp_path):
    path = tmp_path / "input.pdf"
    make_pdf(path)
    result = MarkingPreviewRenderer().render(replace(request(path, MarkingMode.NAMED_MATCHES), search_str="UNKNOWN"))
    assert result.page_number is None
    assert result.no_named_matches


@pytest.mark.parametrize(
    "mode, expected",
    [
        (MarkingMode.ALL_MATCHES, ["#FFFFA6", "#FFFFA6"]),
        (MarkingMode.DISTINGUISH_NAMES, ["#C4FAF8", "#FFFFA6"]),
        (MarkingMode.NAMED_MATCHES, ["#C4FAF8"]),
    ],
)
@pytest.mark.parametrize("language", ["de", "en"])
def test_realistic_example_has_columns_and_marks_default_name_only(monkeypatch, mode, expected, language):
    renderer = MarkingPreviewRenderer()
    render_page = renderer._render_page

    def inspect_page(page, clip):
        text = page.get_text()
        headers = (
            ("Lauf 1/3 - ca. 10:30 Uhr", "Bahn", "Schwimmer", "Jg.", "Verein", "Meldezeit", "01:03,45")
            if language == "de"
            else ("Heat 1/3 - approx. 10:30", "Lane", "Swimmer", "YOB", "Club", "Entry time", "01:03.45")
        )
        for value in (*headers, "Leon Wagner", "Paul Richter"):
            assert value in text
        annotations = list(page.annots())
        assert len(annotations) == len(expected)
        for annotation, color in zip(annotations, expected):
            assert annotation.colors["stroke"] == pytest.approx([int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)])
        times = [pymupdf.Rect(word[:4]) for word in page.get_text("words") if word[4].startswith("01:")]
        assert len(times) == 4
        assert max(rect.x1 for rect in times) - min(rect.x1 for rect in times) < 0.1
        return render_page(page, clip)

    monkeypatch.setattr(renderer, "_render_page", inspect_page)
    assert renderer.render(request(mode=mode, names=(), language=language)).page_number is None
