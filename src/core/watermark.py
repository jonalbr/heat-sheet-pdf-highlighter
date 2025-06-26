"""
Watermark functionality
"""
from typing import Dict

from pymupdf import Page, Rect, utils
from PIL import ImageFont, ImageDraw


def add_watermark(page: Page, text: str, font_size: int, color_hex: str, position: str):
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
    if position == "top":
        text_x = rect.x0 + (rect.width - text_width) / 2
        text_y = rect.y0 + 20
    elif position == "bottom":
        text_x = rect.x0 + (rect.width - text_width) / 2
        text_y = rect.y1 - text_height - 20
    else:
        # Default to top if position is invalid
        text_x = rect.x0 + (rect.width - text_width) / 2
        text_y = rect.y0 + 20
    utils.insert_text(page, (text_x, text_y), text, fontsize=font_size, color=(r, g, b))


def watermark_pdf_page(page: Page, settings: Dict):
    """
    Applies watermark on a PDF page using settings.
    """
    if settings.get("watermark_enabled") == "True" and settings.get("watermark_text"):
        text = settings.get("watermark_text")
        font_size = int(settings.get("watermark_size", 16))
        color_hex = settings.get("watermark_color")
        position = settings.get("watermark_position")
        if text and font_size > 0 and color_hex and position in ["top", "bottom"]:
            add_watermark(page, text=text, font_size=font_size, color_hex=color_hex, position=position)


def overlay_watermark_on_image(image, text: str, font_size: int, color_hex: str, position: str):
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
    if position == "top":
        pos = ((img_width - text_width) / 2, 10)
    else:  # bottom
        pos = ((img_width - text_width) / 2, img_height - text_height - 10)
    # Solid color (opacity set to full)
    draw.text(pos, text, font=font, fill=color_hex)
    return image
