"""Validation and thumbnail generation for platform-managed entity logos.

The original upload is retained for portable Solution export.  The presentation
copy produced here is deliberately small, square, and format-independent so
list cards, favicons, and app headers never need to transfer the source asset.
"""

from __future__ import annotations

import hashlib
import io
import re
import warnings
from dataclasses import dataclass

import cairosvg
from defusedxml import ElementTree as DefusedET
from PIL import Image, ImageOps, UnidentifiedImageError

from shared.svg_sanitizer import SvgSanitizationError, sanitize_svg

LOGO_ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/svg+xml",
}
LOGO_MAX_SOURCE_BYTES = 5 * 1024 * 1024
LOGO_MAX_SOURCE_PIXELS = 25_000_000
LOGO_THUMBNAIL_MAX_DIMENSION = 128
LOGO_THUMBNAIL_TARGET_BYTES = 10 * 1024
LOGO_THUMBNAIL_MAX_BYTES = 20 * 1024
LOGO_THUMBNAIL_CONTENT_TYPE = "image/webp"

_RASTER_FORMATS = {
    "image/jpeg": {"JPEG"},
    "image/jpg": {"JPEG"},
    "image/png": {"PNG"},
}
_SVG_LENGTH = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)")
_THUMBNAIL_VERSION = re.compile(r"^[0-9a-f]{64}$")


class LogoProcessingError(ValueError):
    """Raised when a logo cannot be safely normalized."""


@dataclass(frozen=True)
class ProcessedLogo:
    """Sanitized original plus its bounded presentation thumbnail."""

    original_data: bytes
    original_content_type: str
    thumbnail_data: bytes
    thumbnail_content_type: str
    thumbnail_version: str


def is_logo_thumbnail_version(value: str | None) -> bool:
    """Return whether a stored value is a cacheable thumbnail content hash."""
    return bool(value and _THUMBNAIL_VERSION.fullmatch(value))


def _svg_aspect_ratio(data: bytes) -> float:
    """Read an SVG's intrinsic aspect ratio without resolving external data."""
    try:
        root = DefusedET.fromstring(
            data,
            forbid_dtd=False,
            forbid_entities=True,
            forbid_external=True,
        )
    except Exception as exc:
        raise LogoProcessingError(f"Invalid SVG: {exc}") from exc

    view_box = root.attrib.get("viewBox") or root.attrib.get("viewbox")
    if view_box:
        try:
            _, _, width, height = [float(value) for value in view_box.replace(",", " ").split()]
            if width > 0 and height > 0:
                return width / height
        except (TypeError, ValueError):
            # Fall through to explicit width/height attributes when viewBox is malformed.
            pass

    def length(name: str) -> float | None:
        match = _SVG_LENGTH.match(root.attrib.get(name, ""))
        return float(match.group(1)) if match else None

    width = length("width")
    height = length("height")
    if width and height and width > 0 and height > 0:
        return width / height
    return 1.0


def _svg_to_png(data: bytes) -> bytes:
    """Rasterize a sanitized SVG at a bounded size while preserving its ratio."""
    ratio = _svg_aspect_ratio(data)
    if ratio >= 1:
        width = LOGO_THUMBNAIL_MAX_DIMENSION
        height = max(1, round(width / ratio))
    else:
        height = LOGO_THUMBNAIL_MAX_DIMENSION
        width = max(1, round(height * ratio))

    try:
        rendered = cairosvg.svg2png(
            bytestring=data,
            output_width=width,
            output_height=height,
        )
    except Exception as exc:
        raise LogoProcessingError(f"Could not render SVG: {exc}") from exc
    if not isinstance(rendered, bytes):
        raise LogoProcessingError("SVG renderer returned no image data")
    return rendered


def _open_raster(data: bytes, content_type: str) -> Image.Image:
    """Decode a raster image, rejecting mismatched formats and pixel bombs."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            source = Image.open(io.BytesIO(data))
            expected = _RASTER_FORMATS.get(content_type)
            if expected and source.format not in expected:
                raise LogoProcessingError(
                    f"Image bytes are {source.format or 'unknown'}, not {content_type}"
                )
            if source.width * source.height > LOGO_MAX_SOURCE_PIXELS:
                raise LogoProcessingError(
                    f"Image exceeds the {LOGO_MAX_SOURCE_PIXELS:,}-pixel limit"
                )
            source.load()
            return ImageOps.exif_transpose(source).convert("RGBA")
    except LogoProcessingError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise LogoProcessingError("Image dimensions are too large") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise LogoProcessingError(f"Invalid image: {exc}") from exc


def _square_canvas(source: Image.Image, dimension: int) -> Image.Image:
    """Contain an image in a transparent square without cropping or stretching."""
    contained = source.copy()
    contained.thumbnail((dimension, dimension), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (dimension, dimension), (0, 0, 0, 0))
    offset = ((dimension - contained.width) // 2, (dimension - contained.height) // 2)
    canvas.alpha_composite(contained, offset)
    return canvas


def _encode_thumbnail(source: Image.Image) -> bytes:
    """Encode near 10 KB when practical and never exceed the hard ceiling."""
    candidates: list[bytes] = []
    for dimension in (128, 112, 96, 80, 64):
        canvas = _square_canvas(source, dimension)

        lossless_buffer = io.BytesIO()
        canvas.save(lossless_buffer, "WEBP", lossless=True, method=6, exact=True)
        lossless = lossless_buffer.getvalue()
        candidates.append(lossless)
        if len(lossless) <= LOGO_THUMBNAIL_TARGET_BYTES:
            return lossless

        for quality in (88, 82, 76, 70, 64, 58, 52, 46, 40):
            buffer = io.BytesIO()
            canvas.save(buffer, "WEBP", quality=quality, method=6, exact=True)
            encoded = buffer.getvalue()
            candidates.append(encoded)
            if len(encoded) <= LOGO_THUMBNAIL_TARGET_BYTES:
                return encoded

    within_limit = next(
        (
            candidate
            for candidate in candidates
            if len(candidate) <= LOGO_THUMBNAIL_MAX_BYTES
        ),
        None,
    )
    if within_limit:
        return within_limit
    raise LogoProcessingError(
        f"Could not create a thumbnail below {LOGO_THUMBNAIL_MAX_BYTES // 1024} KB"
    )


def process_logo(data: bytes, content_type: str) -> ProcessedLogo:
    """Validate an upload and create its centered, square presentation copy."""
    if content_type not in LOGO_ALLOWED_CONTENT_TYPES:
        raise LogoProcessingError(
            f"Invalid file type. Allowed: {', '.join(sorted(LOGO_ALLOWED_CONTENT_TYPES))}"
        )
    if not data:
        raise LogoProcessingError("Logo is empty")
    if len(data) > LOGO_MAX_SOURCE_BYTES:
        raise LogoProcessingError(
            f"File too large. Maximum size: {LOGO_MAX_SOURCE_BYTES // 1024 // 1024} MB"
        )

    original = data
    raster_content_type = content_type
    if content_type == "image/svg+xml":
        try:
            original = sanitize_svg(data)
        except SvgSanitizationError as exc:
            raise LogoProcessingError(f"Invalid SVG: {exc}") from exc
        raster_data = _svg_to_png(original)
        raster_content_type = "image/png"
    else:
        raster_data = data

    source = _open_raster(raster_data, raster_content_type)
    thumbnail = _encode_thumbnail(source)
    version = hashlib.sha256(thumbnail).hexdigest()
    return ProcessedLogo(
        original_data=original,
        original_content_type=content_type,
        thumbnail_data=thumbnail,
        thumbnail_content_type=LOGO_THUMBNAIL_CONTENT_TYPE,
        thumbnail_version=version,
    )
