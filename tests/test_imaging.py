"""Unit tests for the pure image helpers."""

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
