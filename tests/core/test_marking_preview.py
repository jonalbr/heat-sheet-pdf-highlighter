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
    monkeypatch.setattr(renderer, "step_query", lambda: pytest.fail("Color change searched again"))
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
    irrelevant_allowed = renderer.render(replace(request(path, MarkingMode.ALL_MATCHES, names=()), only_relevant=False))
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
        patch.setattr(renderer.index, "prepare_next", lambda: pytest.fail("Repeated document extraction"))
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



def make_rows(path, pages):
    with pymupdf.open() as document:
        for lines in pages:
            page = document.new_page()
            for y, text in lines:
                page.insert_text((40, y), text, fontsize=10)
        document.save(path)


def test_progressive_single_result_upgrades_to_nearby_mixed_and_stops(tmp_path, monkeypatch):
    path = tmp_path / "meet.pdf"
    make_pdf(path)
    renderer = MarkingPreviewRenderer()
    renderer.prepare(str(path))
    renderer.prepare_next()
    renderer.start(request(path))
    first = None
    while first is None:
        first = renderer.step_query()
    assert first.state == "searching" and first.page_number == 1
    renderer.prepare_next()
    mixed = None
    while not renderer.query.done:
        mixed = renderer.step_query() or mixed
    assert mixed.state == "ready"
    assert len(mixed.snippets) == 1 and mixed.page_number == 2
    monkeypatch.setattr(renderer.index, "native", lambda *args: pytest.fail("Searched after suitable pair"))
    assert renderer.step_query() is None
    renderer.close()


def test_unrelated_name_addition_keeps_area_and_does_not_read_source(tmp_path, monkeypatch):
    path = tmp_path / "meet.pdf"
    make_pdf(path)
    renderer = MarkingPreviewRenderer()
    original = request(path)
    renderer.render(original)
    selection = renderer.selection
    for native in renderer.index.hot.values():
        monkeypatch.setattr(native.page, "get_text", lambda *args, **kwargs: pytest.fail("Re-extracted PDF text"))
    monkeypatch.setattr(renderer, "step_query", lambda: pytest.fail("Re-selected a suitable area"))
    monkeypatch.setattr("src.core.marking_preview.PreviewSearch.add_page",
                        lambda *args: pytest.fail("Classified other pages before checking current area"))
    renderer.render(replace(original, names=("Alice", "Unrelated Person")))
    assert renderer.selection == selection
    renderer.render(replace(original, highlight_color_default="#AABBCC"))
    assert renderer.selection == selection
    renderer.close()


def test_category_loss_checks_other_visible_rows_before_moving(tmp_path):
    path = tmp_path / "meet.pdf"
    make_rows(path, [[(50, "Bahn 1 Alice CLUB 01:00.00"), (70, "Bahn 2 Bob CLUB 01:01.00"),
                      (90, "Bahn 3 Clara CLUB 01:02.00")]])
    renderer = MarkingPreviewRenderer()
    renderer.render(request(path))
    selection = renderer.selection
    result = renderer.render(request(path, names=("Alice", "Bob")))
    assert result.state == "ready"
    assert renderer.selection == selection
    result = renderer.render(request(path, names=("Alice", "Bob", "Clara")))
    assert result.state == "single_category"
    assert renderer.selection == selection
    renderer.close()


def test_far_matches_use_two_real_excerpts_with_page_numbers(tmp_path):
    path = tmp_path / "meet.pdf"
    make_rows(path, [[(50, "Bahn 1 Alice CLUB 01:00.00")], [(400, "Bahn 2 Bob CLUB 01:01.00")]])
    before = path.read_bytes()
    renderer = MarkingPreviewRenderer()
    result = renderer.render(request(path))
    assert [(snippet.page_number, snippet.role) for snippet in result.snippets] == [(1, "named"), (2, "general")]
    assert all(snippet.png.startswith(b"\x89PNG") for snippet in result.snippets)
    assert path.read_bytes() == before
    renderer.close()


def test_removing_current_name_reclassifies_visible_rows_then_finds_remaining_name(tmp_path):
    path = tmp_path / "meet.pdf"
    make_rows(path, [
        [(50, "Bahn 1 Alice CLUB 01:00.00"), (70, "Bahn 2 Bob CLUB 01:01.00")],
        [(50, "Bahn 3 Clara CLUB 01:02.00"), (70, "Bahn 4 David CLUB 01:03.00")],
    ])
    renderer = MarkingPreviewRenderer()
    renderer.render(request(path, names=("Alice", "Clara")))
    selection = renderer.selection
    first = renderer.start(request(path, names=("Clara",)))
    assert first.state == "searching"
    assert renderer.selection == selection
    assert renderer.query.named is None
    assert renderer.query.general is not None
    result = first
    while not renderer.query.done:
        result = renderer.step_query() or result
    assert result.state == "ready" and result.page_number == 2
    assert renderer.query.named[1].text.startswith("Bahn 3 Clara")
    assert renderer.query.general[1].text.startswith("Bahn 4 David")
    renderer.close()


def test_names_only_split_keeps_ordinary_row_unmarked(tmp_path):
    path = tmp_path / "meet.pdf"
    make_rows(path, [[(50, "Bahn 1 Alice CLUB 01:00.00")], [(50, "Bahn 2 Bob CLUB 01:01.00")]])
    renderer = MarkingPreviewRenderer()
    result = renderer.render(request(path, MarkingMode.NAMED_MATCHES))
    with Image.open(BytesIO(result.snippets[1].png)) as image:
        assert all(r == g == b for r, g, b in image.convert("RGB").get_flattened_data())
    with Image.open(BytesIO(result.snippets[0].png)) as image:
        assert any(r != g for r, g, b in image.convert("RGB").get_flattened_data())
    renderer.close()


def test_background_index_finishes_after_query_is_already_suitable(tmp_path):
    path = tmp_path / "meet.pdf"
    make_rows(path, [[(50, "Bahn 1 Alice CLUB 01:00.00"), (70, "Bahn 2 Bob CLUB 01:01.00")]] * 8)
    renderer = MarkingPreviewRenderer()
    renderer.prepare(str(path))
    renderer.prepare_next()
    renderer.start(request(path))
    while not renderer.query.done:
        renderer.step_query()
    selection = renderer.selection
    assert not renderer.index.complete
    while not renderer.index.complete:
        renderer.prepare_next()
    assert renderer.selection == selection
    assert len(renderer.index.pages) == 8
    renderer.close()


def test_fallback_checks_pages_even_when_text_hint_has_no_name(tmp_path):
    path = tmp_path / "meet.pdf"
    make_pdf(path)
    renderer = MarkingPreviewRenderer()
    renderer.prepare(str(path))
    while not renderer.index.complete:
        renderer.prepare_next()
    # Simulate an unhelpful row hint (for example unusual clipping/whitespace).
    from src.core.preview_index import IndexedPage
    renderer.index.pages = [IndexedPage("", page.rows, page.coordinates) for page in renderer.index.pages]
    result = renderer.render(request(path, MarkingMode.NAMED_MATCHES))
    assert not result.no_named_matches
    assert renderer.query.done
    renderer.close()


def test_new_general_term_reuses_prepared_index_and_native_search_rules(tmp_path, monkeypatch):
    path = tmp_path / "meet.pdf"
    make_rows(path, [[(50, "Bahn 1 Alice CLUB 01:00.00"), (70, "Bahn 2 Bob CLUB 01:01.00"),
                      (300, "Bahn 3 Alice OTHER 01:02.00"), (320, "Bahn 4 Carl OTHER 01:03.00")]])
    renderer = MarkingPreviewRenderer()
    original = request(path)
    renderer.render(original)
    pages = tuple(renderer.index.pages)
    monkeypatch.setattr(renderer.index, "prepare_next", lambda: pytest.fail("Rebuilt document index"))
    result = renderer.render(replace(original, search_str="other"))
    assert result.state == "ready"
    assert tuple(renderer.index.pages) == pages
    assert renderer.selection[0].clip[1] > 200
    renderer.close()


def test_native_page_cache_is_bounded_and_pause_releases_handles(tmp_path):
    path = tmp_path / "meet.pdf"
    make_rows(path, [[(50, "Bahn 1 Alice CLUB 01:00.00")]] * 10)
    renderer = MarkingPreviewRenderer()
    renderer.prepare(str(path))
    while not renderer.index.complete:
        renderer.prepare_next()
    for number in range(10):
        renderer.index.native(number)
        assert len(renderer.index.hot) <= 4
    pages = tuple(renderer.index.pages)
    renderer.index.pause()
    assert renderer.index.document is None
    assert not renderer.index.hot
    assert tuple(renderer.index.pages) == pages
    assert renderer.render(request(path)).png
    renderer.close()


@pytest.mark.parametrize("name", ["Müller-Smith", "Zoë", "Anne Marie"])
def test_name_boundaries_and_unicode_use_export_rules(tmp_path, name):
    path = tmp_path / "meet.pdf"
    make_rows(path, [[(50, f"Bahn 1 {name} CLUB 01:00.00"), (70, "Bahn 2 Bob CLUB 01:01.00")]])
    renderer = MarkingPreviewRenderer()
    result = renderer.render(request(path, names=(name,)))
    assert result.state == "ready"
    assert renderer.query.named is not None and renderer.query.general is not None
    renderer.close()


def test_native_dehyphenation_and_duplicate_occurrences_are_preserved(tmp_path):
    path = tmp_path / "meet.pdf"
    make_rows(path, [[(50, "Bahn 1 Alice HAMBUR-"), (64, "GER CLUB 01:00.00"),
                      (100, "Bahn 2 Bob CLUB CLUB 01:01.00")]])
    renderer = MarkingPreviewRenderer()
    result = renderer.render(replace(request(path, MarkingMode.ALL_MATCHES), search_str="HAMBURGER", only_relevant=False))
    assert result.page_number == 1
    assert len(renderer.index.native(0).search("HAMBURGER")) == 2
    result = renderer.render(request(path, MarkingMode.ALL_MATCHES))
    assert result.png
    # One export annotation per rectangle despite multiple occurrences.
    from src.core.pdf_processor import highlight_matching_data
    with pymupdf.open(path) as doc:
        found, _ = highlight_matching_data(doc[0], "CLUB", True)
        assert found == 3
        assert len(list(doc[0].annots())) == 1
    renderer.close()


@pytest.mark.parametrize("names, page", [((), 1), (("Alice",), 2)])
def test_all_matches_uses_names_for_example_selection_when_available(tmp_path, names, page):
    path = tmp_path / "meet.pdf"
    make_pdf(path)
    renderer = MarkingPreviewRenderer()
    assert renderer.render(request(path, MarkingMode.ALL_MATCHES, names=names)).page_number == page
    renderer.close()


@pytest.mark.parametrize("split", [False, True])
def test_all_and_distinguish_modes_choose_same_example_and_only_redraw_on_switch(tmp_path, monkeypatch, split):
    path = tmp_path / "meet.pdf"
    pages = [[(50, "Bahn 1 Bob CLUB 01:00.00")], [(50, "Bahn 2 Alice CLUB 01:01.00")]]
    if not split:
        pages[1].append((70, "Bahn 3 Clara CLUB 01:02.00"))
    make_rows(path, pages)
    renderer = MarkingPreviewRenderer()
    all_matches = renderer.render(request(path, MarkingMode.ALL_MATCHES))
    selection = renderer.selection
    comparison = MarkingPreviewRenderer()
    distinguished = comparison.render(request(path))
    assert comparison.selection == selection
    assert len(all_matches.snippets) == (2 if split else 1)
    assert all_matches.png != distinguished.png
    if split:
        assert all_matches.snippets[1].png == distinguished.snippets[1].png
    monkeypatch.setattr(renderer, "step_query", lambda: pytest.fail("Mode switch searched for a different example"))
    assert renderer.render(request(path)).png == distinguished.png
    assert renderer.selection == selection
    assert renderer.render(request(path, MarkingMode.ALL_MATCHES)).png == all_matches.png
    renderer.close()
    comparison.close()



def test_prepared_and_rendered_source_file_can_be_replaced_while_worker_stays_warm(tmp_path):
    path = tmp_path / "meet.pdf"
    make_pdf(path)
    renderer = MarkingPreviewRenderer()
    renderer.render(request(path))
    assert renderer.index.document is None
    assert renderer.index.hot
    path.rename(tmp_path / "old.pdf")
    make_rows(path, [[(50, "Bahn 1 Alice CLUB 01:00.00"), (70, "Bahn 2 Carl CLUB 01:01.00")]])
    result = renderer.render(request(path))
    assert result.page_number == 1
    assert renderer.index.document is None
    renderer.close()


def test_preparation_yields_to_newly_available_missing_category(tmp_path):
    path = tmp_path / "meet.pdf"
    make_rows(path, [[(50, "Bahn 1 Alice CLUB 01:00.00")]] * 7 + [[(50, "Bahn 2 Bob CLUB 01:01.00")]])
    renderer = MarkingPreviewRenderer()
    renderer.prepare(str(path))
    renderer.prepare_next()
    renderer.start(request(path))
    result = renderer.step_query()
    assert result.state == "searching"
    assert not renderer.query.can_step(renderer.index)
    while not renderer.index.complete:
        renderer.prepare_next()
    while not renderer.query.done:
        result = renderer.step_query() or result
    assert len(result.snippets) == 2
    # It found the complementary page instead of exhaustively verifying all
    # intervening all-named pages before preparing the rest of the document.
    assert len(renderer.query.finished) < 7
    renderer.close()



def test_partial_existing_preview_is_published_before_any_document_hint_scan(tmp_path, monkeypatch):
    path = tmp_path / "meet.pdf"
    make_pdf(path)
    renderer = MarkingPreviewRenderer()
    missing = request(path, names=("Missing",))
    renderer.render(missing)
    monkeypatch.setattr("src.core.marking_preview.PreviewSearch.add_page", lambda *args: pytest.fail("Eager hint scan"))
    result = renderer.start(replace(missing, names=("Missing", "Another Missing")))
    assert result.state == "searching"
    assert not renderer.query.queued
    renderer.close()



def test_word_to_page_hints_jump_to_known_rare_term_without_searching_other_pages(tmp_path, monkeypatch):
    path = tmp_path / "meet.pdf"
    make_rows(path, [[(50, "Bahn 1 Alice CLUB 01:00.00")]] * 9
              + [[(50, "Bahn 1 Alice RARE 01:00.00"), (70, "Bahn 2 Bob RARE 01:01.00")]])
    renderer = MarkingPreviewRenderer()
    renderer.prepare(str(path))
    while not renderer.index.complete:
        renderer.prepare_next()
    visited = []
    original = renderer.index.native

    def native(number):
        visited.append(number)
        return original(number)

    monkeypatch.setattr(renderer.index, "native", native)
    result = renderer.render(replace(request(path), search_str="RARE"))
    assert result.page_number == 10
    assert set(visited) == {9}
    renderer.close()
