"""
Watermark functionality
"""

from typing import Dict

from pymupdf import Page, Rect
from PIL import ImageFont, ImageDraw

from ..constants import WATERMARK_DEFAULT_X_RATIO, WATERMARK_DEFAULT_Y_RATIO

PRESET_POSITION_RATIOS = {
    "top": (WATERMARK_DEFAULT_X_RATIO, WATERMARK_DEFAULT_Y_RATIO),
    "bottom": (WATERMARK_DEFAULT_X_RATIO, 1.0 - WATERMARK_DEFAULT_Y_RATIO),
}
DEFAULT_POSITION_MODE = "top"
DEFAULT_X_RATIO = PRESET_POSITION_RATIOS[DEFAULT_POSITION_MODE][0]
DEFAULT_Y_RATIO = PRESET_POSITION_RATIOS[DEFAULT_POSITION_MODE][1]


def clamp_ratio(value: float) -> float:
    """Clamp a normalized position value into the visible page range."""
    return max(0.0, min(1.0, float(value)))


def get_position_ratios(position: str, x_ratio: float | None = None, y_ratio: float | None = None) -> tuple[float, float]:
    """Return normalized center coordinates for a preset or custom watermark position."""
    if position in PRESET_POSITION_RATIOS:
        return PRESET_POSITION_RATIOS[position]

    if position == "custom":
        return (
            clamp_ratio(DEFAULT_X_RATIO if x_ratio is None else x_ratio),
            clamp_ratio(DEFAULT_Y_RATIO if y_ratio is None else y_ratio),
        )

    return PRESET_POSITION_RATIOS[DEFAULT_POSITION_MODE]


def calculate_text_position(
    container_width: float,
    container_height: float,
    text_width: float,
    text_height: float,
    position: str,
    x_ratio: float | None = None,
    y_ratio: float | None = None,
) -> tuple[float, float]:
    """Return a visible top-left text position from normalized center coordinates."""
    center_x_ratio, center_y_ratio = get_position_ratios(position, x_ratio, y_ratio)
    center_x = center_x_ratio * container_width
    center_y = center_y_ratio * container_height
    max_x = max(0.0, container_width - text_width)
    max_y = max(0.0, container_height - text_height)
    text_x = max(0.0, min(max_x, center_x - text_width / 2))
    text_y = max(0.0, min(max_y, center_y - text_height / 2))
    return text_x, text_y


def add_watermark(
    page: Page,
    text: str,
    font_size: int,
    color_hex: str,
    position: str,
    x_ratio: float | None = None,
    y_ratio: float | None = None,
):
    """
    Adds a watermark on the given PDF page.
    """
    # Convert hex color to normalized RGB tuple
    r = int(color_hex[1:3], 16) / 255
    g = int(color_hex[3:5], 16) / 255
    b = int(color_hex[5:7], 16) / 255
    rect: Rect = page.rect
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()
    bbox = font.getbbox(text)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    text_x, text_y = calculate_text_position(rect.width, rect.height, text_width, text_height, position, x_ratio, y_ratio)
    # Pillow measures top-left text bounds, while PyMuPDF insert_text expects a baseline point.
    page.insert_text((rect.x0 + text_x, rect.y0 + text_y + text_height), text, fontsize=font_size, color=(r, g, b))


def watermark_pdf_page(page: Page, settings: Dict):
    """
    Applies watermark on a PDF page using settings.
    """
    if settings.get("watermark_enabled") == "True" and settings.get("watermark_text"):
        text = settings.get("watermark_text")
        font_size = int(settings.get("watermark_size", 16))
        color_hex = settings.get("watermark_color")
        position = settings.get("watermark_position")
        x_ratio = settings.get("watermark_x_ratio", DEFAULT_X_RATIO)
        y_ratio = settings.get("watermark_y_ratio", DEFAULT_Y_RATIO)
        if text and font_size > 0 and color_hex and position in ["top", "bottom", "custom"]:
            add_watermark(page, text=text, font_size=font_size, color_hex=color_hex, position=position, x_ratio=x_ratio, y_ratio=y_ratio)


def overlay_watermark_on_image(
    image,
    text: str,
    font_size: int,
    color_hex: str,
    position: str,
    x_ratio: float | None = None,
    y_ratio: float | None = None,
):
    """
    Overlay watermark on an image (used for preview).
    """
    draw = ImageDraw.Draw(image, "RGBA")
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        font = ImageFont.load_default()
    # Use textbbox to get dimensions
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    img_width, img_height = image.size
    pos = calculate_text_position(img_width, img_height, text_width, text_height, position, x_ratio, y_ratio)
    # Solid color (opacity set to full)
    draw.text(pos, text, font=font, fill=color_hex)
    return image
