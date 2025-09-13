import pytest

try:
    import pymupdf
except ImportError:  # pragma: no cover
    pymupdf = None

from src.core import pdf_processor as pp
from src.models import HighlightMode

pytestmark = pytest.mark.skipif(pymupdf is None, reason="pymupdf not installed")


def make_doc(text: str):
    doc = pymupdf.open()
    page = doc.new_page()  # default size
    page.insert_text((50, 50), text)
    return doc, page


def collect_highlights(page):
    highlights = []
    annot = page.first_annot
    while annot:
        info = annot.info
        colors = annot.colors
        stroke = colors.get("stroke") if colors else None
        highlights.append({"stroke": stroke, "content": info.get("content")})
        annot = annot.next
    return highlights


def test_no_matches_returns_zero():
    doc, page = make_doc("Nothing relevant here")
    matches, skipped = pp.highlight_matching_data(page, "CLUB", only_relevant=True)
    assert (matches, skipped) == (0, 0)
    doc.close()


def test_relevant_line_highlight_basic():
    text = "Bahn 1 LaneName CLUB 00:59.99"
    doc, page = make_doc(text)
    matches, skipped = pp.highlight_matching_data(page, "CLUB", only_relevant=True)
    assert matches == 1 and skipped == 0
    hl = collect_highlights(page)
    assert len(hl) == 1
    doc.close()


def test_irrelevant_line_skipped():
    text = "Some random line with CLUB but no timing token"
    doc, page = make_doc(text)
    matches, skipped = pp.highlight_matching_data(page, "CLUB", only_relevant=True)
    assert matches == 1 and skipped == 1
    assert collect_highlights(page) == []
    doc.close()


def test_only_names_mode_filters_out_without_names():
    text = "Bahn 2 Random Person CLUB 01:10.00"
    doc, page = make_doc(text)
    matches, skipped = pp.highlight_matching_data(
        page,
        "CLUB",
        only_relevant=True,
        filter_enabled=True,
        names=["Alice"],
        highlight_mode=HighlightMode.ONLY_NAMES,
    )
    assert matches == 1 and skipped == 1
    assert collect_highlights(page) == []
    doc.close()


def test_only_names_mode_highlights_when_name_present():
    text = "Bahn 2 Alice CLUB 01:10.00"
    doc, page = make_doc(text)
    matches, skipped = pp.highlight_matching_data(
        page,
        "CLUB",
        only_relevant=True,
        filter_enabled=True,
        names=["Alice"],
        highlight_mode=HighlightMode.ONLY_NAMES,
    )
    assert matches == 1 and skipped == 0
    assert len(collect_highlights(page)) == 1
    doc.close()


def test_names_diff_color_blue_when_name_present():
    text = "Bahn 3 Alice CLUB 01:10.00"
    doc, page = make_doc(text)
    matches, skipped = pp.highlight_matching_data(
        page,
        "CLUB",
        only_relevant=True,
        filter_enabled=True,
        names=["Alice"],
        highlight_mode=HighlightMode.NAMES_DIFF_COLOR,
    )
    assert matches == 1 and skipped == 0
    hl = collect_highlights(page)
    assert len(hl) == 1
    stroke = hl[0]["stroke"]
    assert stroke and pytest.approx(stroke[0], rel=1e-2) == pytest.approx(196/255, rel=1e-2)
    doc.close()


def test_names_diff_color_yellow_when_name_missing():
    text = "Bahn 4 Bob CLUB 01:10.00"
    doc, page = make_doc(text)
    matches, skipped = pp.highlight_matching_data(
        page,
        "CLUB",
        only_relevant=True,
        filter_enabled=True,
        names=["Alice"],
        highlight_mode=HighlightMode.NAMES_DIFF_COLOR,
    )
    assert matches == 1 and skipped == 0
    hl = collect_highlights(page)
    assert len(hl) == 1
    stroke = hl[0]["stroke"]
    assert stroke and stroke[0] == pytest.approx(1.0) and stroke[2] == pytest.approx(166/255, rel=1e-2)
    doc.close()


def test_names_diff_color_filter_disabled_defaults_yellow():
    text = "Bahn 5 Alice CLUB 01:10.00"
    doc, page = make_doc(text)
    matches, skipped = pp.highlight_matching_data(
        page,
        "CLUB",
        only_relevant=True,
        filter_enabled=False,
        names=["Alice"],
        highlight_mode=HighlightMode.NAMES_DIFF_COLOR,
    )
    assert matches == 1 and skipped == 0
    hl = collect_highlights(page)
    stroke = hl[0]["stroke"]
    assert stroke and stroke[2] == pytest.approx(166/255, rel=1e-2)  # yellow path
    doc.close()


def test_only_relevant_false_highlights_any_match():
    text = "Totally random CLUB token"
    doc, page = make_doc(text)
    matches, skipped = pp.highlight_matching_data(page, "CLUB", only_relevant=False)
    assert matches == 1 and skipped == 0
    assert len(collect_highlights(page)) == 1
    doc.close()


def test_line_text_normalization_list_and_dict(monkeypatch):
    # Use real Rect objects for compatibility with get_line_bbox
    class FakeHighlight:
        def __init__(self):
            self.colors = {}
        def set_colors(self, stroke):
            self.colors['stroke'] = stroke
        def update(self):
            pass

    class FakePage:
        def __init__(self):
            self.highlights = []
        def add_highlight_annot(self, rect):
            h = FakeHighlight()
            self.highlights.append(h)
            return h

    fake_page = FakePage()

    calls = {"phase": 0}

    def fake_search_for(page, needle):
        # Return one match rect
        return [pp.Rect(0, 0, 10, 10)]

    def fake_get_text(page, mode, clip=None):
        if mode == "words":
            # Minimal word entries: [x0,y0,x1,y1, word, block_no, line_no, word_no]
            return [
                [0, 0, 5, 10, "Bahn", 0, 0, 0],
                [6, 0, 15, 10, "5", 0, 0, 1],
                [16, 0, 40, 10, "Alice", 0, 0, 2],
                [41, 0, 60, 10, "CLUB", 0, 0, 3],
                [61, 0, 90, 10, "01:10.00", 0, 0, 4],
            ]
        # For 'text' mode exercise list then dict normalization
        if calls["phase"] == 0:
            calls["phase"] = 1
            return ["Bahn 5", "Alice", "CLUB", "01:10.00"]
        else:
            return {"line": "Bahn 5 Alice CLUB 01:10.00"}

    monkeypatch.setattr(pp.utils, "search_for", fake_search_for)
    monkeypatch.setattr(pp.utils, "get_text", fake_get_text)

    matches, skipped = pp.highlight_matching_data(
        fake_page,
        "CLUB",
        only_relevant=True,
        filter_enabled=True,
        names=["Alice"],
        highlight_mode=HighlightMode.NAMES_DIFF_COLOR,
    )

    assert matches == 1
    assert skipped == 0
    assert len(fake_page.highlights) == 1

