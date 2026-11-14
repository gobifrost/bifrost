"""Source-built image packages retain the capabilities of the previous wheels."""

import io

import pytest
from PIL import Image, features


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP", "TIFF", "JPEG2000", "AVIF"])
def test_image_codec_round_trip(format):
    encoded = io.BytesIO()
    Image.new("RGB", (8, 8), color="red").save(encoded, format=format)
    encoded.seek(0)
    with Image.open(encoded) as decoded:
        decoded.load()
        assert decoded.size == (8, 8)
        assert decoded.format == format


@pytest.mark.parametrize("feature", ["freetype2", "raqm", "littlecms2"])
def test_image_text_and_color_capabilities(feature):
    assert features.check(feature)
