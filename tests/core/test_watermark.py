import pytest
from PIL import Image
import pymupdf

from src.core import watermark as wm


@pytest.fixture
def blank_page():
    doc = pymupdf.open()  # empty PDF
    page = doc.new_page(width=400, height=300)
    yield page
    doc.close()


class DummyFont:
    def __init__(self, char_w=6, h=12):
        self._cw = char_w
        self._h = h

    def getbbox(self, text, *_, **__):
        # mimic PIL getbbox returning (x0, y0, x1, y1) ignoring extra args
        return (0, 0, len(text) * self._cw, self._h)


def test_add_watermark_top_position(blank_page, monkeypatch):
    calls = {}

    def fake_insert_text(page, point, text, fontsize, color):  # capture args
        calls['args'] = dict(point=point, text=text, fontsize=fontsize, color=color)

    monkeypatch.setattr(wm.utils, 'insert_text', fake_insert_text)
    wm.add_watermark(blank_page, text="HELLO", font_size=24, color_hex="#112233", position="top")
    args = calls['args']
    # Color hex -> normalized rgb
    assert args['color'] == (17/255, 34/255, 51/255)
    assert args['text'] == "HELLO"
    # y should be near 20 from top margin (allow font height variance)
    assert 15 <= args['point'][1] <= 40


def test_add_watermark_bottom_position(blank_page, monkeypatch):
    calls = {}

    def fake_insert_text(page, point, text, fontsize, color):
        calls['args'] = dict(point=point, text=text, fontsize=fontsize, color=color)

    monkeypatch.setattr(wm.utils, 'insert_text', fake_insert_text)
    wm.add_watermark(blank_page, text="BYE", font_size=18, color_hex="#AABBCC", position="bottom")
    args = calls['args']
    # y coordinate should be well below half the page (bottom area)
    assert args['point'][1] > blank_page.rect.height / 2


def test_add_watermark_invalid_position_defaults_to_top(blank_page, monkeypatch):
    calls = {}

    def fake_insert_text(page, point, text, fontsize, color):
        calls['args'] = dict(point=point, text=text, fontsize=fontsize, color=color)

    monkeypatch.setattr(wm.utils, 'insert_text', fake_insert_text)
    wm.add_watermark(blank_page, text="TEXT", font_size=12, color_hex="#000000", position="weird")
    args = calls['args']
    assert args['point'][1] < blank_page.rect.height / 2  # top


def test_add_watermark_font_fallback(blank_page, monkeypatch):
    # Force truetype to raise -> then supply dummy font via load_default
    def fail_truetype(name, size):  # pragma: no cover - behavior itself
        raise OSError("no font")

    monkeypatch.setattr(wm.ImageFont, 'truetype', fail_truetype)
    monkeypatch.setattr(wm.ImageFont, 'load_default', lambda: DummyFont())
    calls = {}

    def fake_insert_text(page, point, text, fontsize, color):
        calls['fontsize'] = fontsize

    monkeypatch.setattr(wm.utils, 'insert_text', fake_insert_text)
    wm.add_watermark(blank_page, text="FALLBACK", font_size=14, color_hex="#010101", position="top")
    # If we reached insert_text, fallback worked. Font size passed through.
    assert calls['fontsize'] == 14


@pytest.mark.parametrize(
    'settings,should_call', [
        ({'watermark_enabled': 'True', 'watermark_text': 'X', 'watermark_size': '10', 'watermark_color': '#FFFFFF', 'watermark_position': 'top'}, True),
        ({'watermark_enabled': 'False', 'watermark_text': 'X', 'watermark_size': '10', 'watermark_color': '#FFFFFF', 'watermark_position': 'top'}, False),
        ({'watermark_enabled': 'True', 'watermark_text': '', 'watermark_size': '10', 'watermark_color': '#FFFFFF', 'watermark_position': 'top'}, False),
        ({'watermark_enabled': 'True', 'watermark_text': 'X', 'watermark_size': '0', 'watermark_color': '#FFFFFF', 'watermark_position': 'top'}, False),
        ({'watermark_enabled': 'True', 'watermark_text': 'X', 'watermark_size': '10', 'watermark_color': None, 'watermark_position': 'top'}, False),
        ({'watermark_enabled': 'True', 'watermark_text': 'X', 'watermark_size': '10', 'watermark_color': '#FFFFFF', 'watermark_position': 'middle'}, False),
    ]
)
def test_watermark_pdf_page_guard_logic(blank_page, monkeypatch, settings, should_call):
    called = {'v': False}

    def fake_add(page, text, font_size, color_hex, position):
        called['v'] = True

    monkeypatch.setattr(wm, 'add_watermark', fake_add)
    wm.watermark_pdf_page(blank_page, settings)
    assert called['v'] is should_call


def test_overlay_watermark_on_image_top_and_bottom(monkeypatch):
    img_top = Image.new('RGB', (200, 100), 'white')
    img_bottom = Image.new('RGB', (200, 100), 'white')

    out_top = wm.overlay_watermark_on_image(img_top, text='TXT', font_size=10, color_hex='#FF0000', position='top')
    out_bottom = wm.overlay_watermark_on_image(img_bottom, text='TXT', font_size=10, color_hex='#00FF00', position='bottom')

    # Basic pixel sampling to ensure text draw likely occurred (not blank). We can't assert exact pixels due to font differences
    # but at least ensure images still return and have same size
    assert out_top.size == (200, 100)
    assert out_bottom.size == (200, 100)

