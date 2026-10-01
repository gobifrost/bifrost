import pytest

from src.services.source_search.matching import build_matcher, iter_line_hits, window_line


def match_lines(content, matcher, context_lines):
    return list(iter_line_hits(content, matcher, context_lines))


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


class _CountingMatcher:
    """Wraps a compiled pattern and records which lines were scanned."""

    def __init__(self, pattern: str):
        import re

        self._re = re.compile(pattern)
        self.scanned: list[str] = []

    def finditer(self, line: str):
        self.scanned.append(line)
        return self._re.finditer(line)


def test_hits_are_produced_lazily_so_a_page_stops_early():
    from itertools import islice

    from src.services.source_search.matching import iter_line_hits

    content = "\n".join(f"e{i}" for i in range(100_000))
    matcher = _CountingMatcher("e")
    hits = list(islice(iter_line_hits(content, matcher, context_lines=1), 3))
    assert [h.line for h in hits] == [1, 2, 3]
    assert len(matcher.scanned) == 3


def test_resume_position_skips_earlier_lines_without_scanning_them():
    from itertools import islice

    from src.services.source_search.matching import iter_line_hits

    content = "\n".join("ab ab" for _ in range(1000))
    matcher = _CountingMatcher("ab")
    hits = list(islice(iter_line_hits(content, matcher, context_lines=0, after=(500, 0)), 2))
    assert [(h.line, h.column) for h in hits] == [(500, 3), (501, 0)]
    assert matcher.scanned[0] == "ab ab" and len(matcher.scanned) == 2


def test_count_matches_counts_without_building_hits():
    from src.services.source_search.matching import count_matches

    assert count_matches("x\nfoo foo\nbar\nfoo", build_matcher("foo", False, True)) == (3, 2)
    assert count_matches("abc\ndef", build_matcher("x*", True, True)) == (2, 1)
    assert count_matches("abc", build_matcher("zzz", False, True)) == (0, 0)
