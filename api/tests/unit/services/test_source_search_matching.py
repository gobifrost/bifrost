import pytest

from src.services.source_search.matching import build_matcher, match_lines, window_line


def test_literal_is_escaped_and_case_insensitive():
    m = build_matcher("a.b", is_regex=False, case_sensitive=False)
    assert [h.line for h in match_lines("x\nA.B\naxb", m, context_lines=0)] == [2]


def test_every_occurrence_on_a_line_is_a_hit_with_its_column():
    hits = match_lines("foo foo", build_matcher("foo", False, True), context_lines=0)
    assert [(h.line, h.column) for h in hits] == [(1, 0), (1, 4)]


def test_crlf_is_normalised_and_context_collected():
    hits = match_lines("a\r\nTARGET\r\nc\r\n", build_matcher("TARGET", False, True), context_lines=1)
    assert hits[0].text == "TARGET" and hits[0].context_before == ["a"] and hits[0].context_after == ["c"]


def test_context_is_clipped_at_file_edges():
    hits = match_lines("TARGET", build_matcher("TARGET", False, True), context_lines=3)
    assert (hits[0].context_before, hits[0].context_after) == ([], [])


def test_long_line_is_windowed():
    line = "x" * 5000 + "NEEDLE" + "y" * 5000
    text = window_line(line, column=5000)
    assert "NEEDLE" in text and len(text) <= 402 and text.startswith("…") and text.endswith("…")


def test_short_line_is_untouched():
    assert window_line("short", column=0) == "short"


def test_invalid_regex_raises_value_error():
    with pytest.raises(ValueError, match="Invalid regex"):
        build_matcher("(", is_regex=True, case_sensitive=False)


def test_empty_width_regex_matches_do_not_flood():
    hits = match_lines("abc", build_matcher("x*", True, True), context_lines=0)
    assert len(hits) <= 1
