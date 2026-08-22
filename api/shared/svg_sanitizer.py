"""SVG sanitizer.

Parses SVG bytes with defusedxml (blocking XXE / billion-laughs), strips
active content, and prevents external resource resolution. Returns sanitized
bytes ready to store, serve, or rasterize on the backend.
"""

from __future__ import annotations

import re
from xml.etree.ElementTree import Element, register_namespace, tostring

from defusedxml import ElementTree as DefusedET

_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"
_XML_NS = "http://www.w3.org/XML/1998/namespace"
_CSS_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
_EMBEDDED_RASTER = re.compile(
    r"^\s*data:image/(?:png|jpeg|jpg|gif|webp);base64,",
    re.IGNORECASE,
)

# Register common SVG namespaces so tostring() emits clean tag names
# (e.g. <svg> instead of <ns0:svg>).
register_namespace("", _SVG_NS)
register_namespace("xlink", _XLINK_NS)


class SvgSanitizationError(ValueError):
    """Raised when the SVG cannot be safely parsed or sanitized."""


def _safe_resource_reference(value: str) -> bool:
    """Allow document fragments and embedded raster images, never I/O."""
    stripped = value.strip()
    return stripped.startswith("#") or bool(_EMBEDDED_RASTER.match(stripped))


def _has_external_css_reference(value: str) -> bool:
    """Detect CSS capable of loading a resource outside this document."""
    if "@import" in value.lower():
        return True
    return any(not _safe_resource_reference(match.group(2)) for match in _CSS_URL.finditer(value))


def _strip(element: Element) -> None:
    # Remove disallowed attributes
    for attr in list(element.attrib.keys()):
        local = attr.split("}")[-1].lower()
        if local.startswith("on"):
            del element.attrib[attr]
            continue
        if attr == f"{{{_XML_NS}}}base":
            del element.attrib[attr]
            continue
        if local == "href" or attr == f"{{{_XLINK_NS}}}href":
            if not _safe_resource_reference(element.attrib[attr]):
                del element.attrib[attr]
                continue
        if _has_external_css_reference(element.attrib[attr]):
            del element.attrib[attr]

    # Recurse, removing forbidden children in place.
    for child in list(element):
        tag = child.tag.split("}")[-1].lower()
        if tag in ("script", "link"):
            element.remove(child)
            continue
        if tag == "style" and _has_external_css_reference(child.text or ""):
            element.remove(child)
            continue
        _strip(child)


def sanitize_svg(data: bytes) -> bytes:
    """Return a sanitized copy of the SVG bytes.

    Raises SvgSanitizationError if the input can't be safely parsed.
    """
    try:
        # Allow a benign top-level DOCTYPE (Inkscape/Illustrator emit one
        # referencing the SVG 1.1 DTD), but reject entity declarations and
        # external resolution — those are the actual XXE / billion-laughs
        # attack vectors.
        root = DefusedET.fromstring(
            data,
            forbid_dtd=False,
            forbid_entities=True,
            forbid_external=True,
        )
    except Exception as exc:
        raise SvgSanitizationError(f"unparseable svg: {exc}") from exc

    _strip(root)

    # Re-serialize using stdlib tostring (defusedxml doesn't provide its own).
    return tostring(root, encoding="unicode").encode("utf-8")
