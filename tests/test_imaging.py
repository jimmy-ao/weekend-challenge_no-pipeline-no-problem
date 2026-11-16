"""Unit tests for the pure image helpers."""

import io
import os
import sys

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend", "functions", "pipeline"))

import imaging  # noqa: E402


@pytest.mark.parametrize("src", [(502, 1506), (509, 900), (788, 2000), (1506, 502)])
@pytest.mark.parametrize("focus", [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)])
def test_cover_crop_survives_float_rounding(src, focus):
    # 1080 / (1080 / 502) == 502.00000000000006: the crop box used to start at -5.7e-14,
    # which Pillow rejects with "box offset can't be negative".
    out = imaging.cover_crop(Image.new("RGB", src), 1080, 1080, *focus)
    assert out.size == (1080, 1080)


def _encoded(img, fmt):
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def test_oversized_images_are_refused():
    # 1-bit PNGs: a few KB on disk, hundreds of MB once decoded as RGBA.
    event_image = _encoded(Image.new("1", (7000, 6000)), "PNG")  # 42 MP
    with pytest.raises(ValueError, match="megapixels"):
        imaging.load_logo(event_image)
    assert imaging.load_photo(event_image).size == (7000, 6000)  # still fine as a photo
    with pytest.raises(Exception):  # ValueError here, DecompressionBombError past twice the limit
        imaging.load_photo(_encoded(Image.new("1", (11000, 11000)), "PNG"))  # 121 MP


def test_only_jpeg_png_webp_are_decoded():
    for fmt in ("JPEG", "PNG", "WEBP"):
        assert imaging.load_photo(_encoded(Image.new("RGB", (40, 30)), fmt)).size == (40, 30)
    for fmt in ("GIF", "BMP", "TIFF"):
        with pytest.raises(Exception):
            imaging.load_photo(_encoded(Image.new("RGB", (40, 30)), fmt))
