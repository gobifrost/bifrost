"""Focused tests for platform-managed logo normalization."""

from __future__ import annotations

import io

from PIL import Image

from shared.logo_processing import (
    LOGO_THUMBNAIL_MAX_BYTES,
    LOGO_THUMBNAIL_TARGET_BYTES,
    LogoProcessingError,
    process_logo,
)


def _png(width: int, height: int, color: tuple[int, int, int, int]) -> bytes:
    image = Image.new("RGBA", (width, height), color)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _thumbnail(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data)).convert("RGBA")
    image.load()
    return image


def test_wide_logo_is_contained_and_centered_on_transparent_square() -> None:
    processed = process_logo(_png(400, 100, (220, 20, 60, 255)), "image/png")
    image = _thumbnail(processed.thumbnail_data)

    assert image.width == image.height
    alpha_bounds = image.getchannel("A").getbbox()
    assert alpha_bounds is not None
    left, top, right, bottom = alpha_bounds
    assert left == 0
    assert right == image.width
    assert abs(top - (image.height - bottom)) <= 1
    assert bottom - top == image.width // 4


def test_tall_logo_is_contained_and_centered_on_transparent_square() -> None:
    processed = process_logo(_png(80, 320, (20, 120, 220, 255)), "image/png")
    image = _thumbnail(processed.thumbnail_data)

    assert image.width == image.height
    alpha_bounds = image.getchannel("A").getbbox()
    assert alpha_bounds is not None
    left, top, right, bottom = alpha_bounds
    assert top == 0
    assert bottom == image.height
    assert abs(left - (image.width - right)) <= 1
    assert right - left == image.height // 4


def test_small_logo_is_not_upscaled_inside_canvas() -> None:
    processed = process_logo(_png(24, 12, (0, 0, 0, 255)), "image/png")
    image = _thumbnail(processed.thumbnail_data)

    assert image.getchannel("A").getbbox() == (52, 58, 76, 70)


def test_thumbnail_meets_transfer_budget_and_has_stable_version() -> None:
    source = _png(512, 256, (120, 70, 210, 255))
    first = process_logo(source, "image/png")
    second = process_logo(source, "image/png")

    assert len(first.thumbnail_data) <= LOGO_THUMBNAIL_TARGET_BYTES
    assert len(first.thumbnail_data) <= LOGO_THUMBNAIL_MAX_BYTES
    assert first.thumbnail_content_type == "image/webp"
    assert first.thumbnail_version == second.thumbnail_version


def test_svg_is_rasterized_without_stretching() -> None:
    source = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 100">
      <rect width="400" height="100" fill="#ff0055"/>
    </svg>'''
    processed = process_logo(source, "image/svg+xml")
    image = _thumbnail(processed.thumbnail_data)

    assert image.width == image.height
    bounds = image.getchannel("A").getbbox()
    assert bounds is not None
    assert bounds[1] > 0
    assert bounds[3] < image.height


def test_rejects_content_type_mismatch() -> None:
    try:
        process_logo(_png(32, 32, (0, 0, 0, 255)), "image/jpeg")
    except LogoProcessingError as exc:
        assert "not image/jpeg" in str(exc)
    else:
        raise AssertionError("Expected mismatched content type to be rejected")
