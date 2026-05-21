"""Unit tests for SVG sanitizer."""

import pytest

from shared.svg_sanitizer import SvgSanitizationError, sanitize_svg


CLEAN_SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><circle cx="5" cy="5" r="4"/></svg>'


def test_clean_svg_round_trips():
    out = sanitize_svg(CLEAN_SVG)
    assert b"<circle" in out
    assert b"<svg" in out


def test_script_element_removed():
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script><circle r="1"/></svg>'
    out = sanitize_svg(payload)
    assert b"script" not in out.lower()
    assert b"<circle" in out


def test_event_handler_attribute_removed():
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="1" onclick="alert(1)" onload="x()"/></svg>'
    out = sanitize_svg(payload)
    assert b"onclick" not in out.lower()
    assert b"onload" not in out.lower()
    assert b"<circle" in out


def test_javascript_href_removed():
    payload = b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><circle r="1"/></a></svg>'
    out = sanitize_svg(payload)
    assert b"javascript:" not in out.lower()


def test_xlink_javascript_href_removed():
    payload = (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<a xlink:href="javascript:alert(1)"><circle r="1"/></a></svg>'
    )
    out = sanitize_svg(payload)
    assert b"javascript:" not in out.lower()


def test_xxe_blocked():
    payload = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE svg [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>&xxe;</text></svg>'
    )
    with pytest.raises(SvgSanitizationError):
        sanitize_svg(payload)


def test_malformed_xml_rejected():
    with pytest.raises(SvgSanitizationError):
        sanitize_svg(b"<svg><not-closed>")


def test_inkscape_style_doctype_accepted():
    """SVG editors (Inkscape, Illustrator) emit a benign DOCTYPE referencing the
    SVG 1.1 DTD; we must accept it because real-world logo files commonly include
    it. The XXE / billion-laughs vectors are entity declarations and external
    resolution, both of which remain blocked."""
    payload = (
        b'<?xml version="1.0" standalone="no"?>'
        b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
        b'"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        b'<circle cx="5" cy="5" r="4"/></svg>'
    )
    out = sanitize_svg(payload)
    assert b"<circle" in out


def test_billion_laughs_blocked():
    payload = (
        b'<?xml version="1.0"?>'
        b'<!DOCTYPE lolz ['
        b'<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        b']>'
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>&lol2;</text></svg>'
    )
    with pytest.raises(SvgSanitizationError):
        sanitize_svg(payload)
