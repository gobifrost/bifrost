"""SVG sanitizer.

Parses SVG bytes with defusedxml (blocking XXE / billion-laughs), strips
script elements, event-handler attributes, and javascript: hrefs. Returns
sanitized bytes ready to store and serve.
"""

from __future__ import annotations

import re
from xml.etree.ElementTree import Element, register_namespace, tostring

from defusedxml import ElementTree as DefusedET

_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK_NS = "http://www.w3.org/1999/xlink"
_JS_URI = re.compile(r"^\s*javascript:", re.IGNORECASE)

# Register common SVG namespaces so tostring() emits clean tag names
# (e.g. <svg> instead of <ns0:svg>).
register_namespace("", _SVG_NS)
register_namespace("xlink", _XLINK_NS)


class SvgSanitizationError(ValueError):
    """Raised when the SVG cannot be safely parsed or sanitized."""


def _strip(element: Element) -> None:
    # Remove disallowed attributes
    for attr in list(element.attrib.keys()):
        local = attr.split("}")[-1].lower()
        if local.startswith("on"):
            del element.attrib[attr]
            continue
        if local == "href" or attr == f"{{{_XLINK_NS}}}href":
            if _JS_URI.match(element.attrib[attr]):
                del element.attrib[attr]

    # Recurse, removing forbidden children in place
    for child in list(element):
        tag = child.tag.split("}")[-1].lower()
        if tag in ("script",):
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
