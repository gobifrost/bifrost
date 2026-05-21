"""Tests for the _search_content pure function in src.services.editor.search."""

import pytest

from src.services.editor.search import _search_content, _validate_regex_pattern


class TestSimpleSearch:
    """Basic text search functionality."""

    def test_finds_match_with_correct_line_number(self):
        content = "hello world"
        results = _search_content(content, "test.py", "hello", case_sensitive=False, is_regex=False)
        assert len(results) == 1
        assert results[0].line == 1
        assert results[0].column == 0
        assert results[0].match_text == "hello world"
        assert results[0].file_path == "test.py"

    def test_no_matches_returns_empty_list(self):
        content = "hello world"
        results = _search_content(content, "test.py", "missing", case_sensitive=False, is_regex=False)
        assert results == []

    def test_empty_content_returns_empty(self):
        results = _search_content("", "test.py", "hello", case_sensitive=False, is_regex=False)
        assert results == []


class TestCaseSensitivity:
    """Case sensitivity behavior."""

    def test_case_insensitive_finds_different_case(self):
        content = "Hello World"
        results = _search_content(content, "test.py", "hello", case_sensitive=False, is_regex=False)
        assert len(results) == 1

    def test_case_sensitive_misses_different_case(self):
        content = "Hello World"
        results = _search_content(content, "test.py", "hello", case_sensitive=True, is_regex=False)
        assert len(results) == 0

    def test_case_sensitive_finds_exact_case(self):
        content = "Hello World"
        results = _search_content(content, "test.py", "Hello", case_sensitive=True, is_regex=False)
        assert len(results) == 1


class TestRegexSearch:
    """Regex pattern search functionality."""

    def test_regex_digit_pattern(self):
        content = "line with 42 numbers"
        results = _search_content(content, "test.py", r"\d+", case_sensitive=False, is_regex=True)
        assert len(results) == 1
        assert results[0].column == 10

    def test_regex_word_boundary(self):
        content = "foo bar baz"
        results = _search_content(content, "test.py", r"\bbar\b", case_sensitive=False, is_regex=True)
        assert len(results) == 1
        assert results[0].column == 4

    def test_invalid_regex_returns_empty(self):
        content = "hello world"
        results = _search_content(content, "test.py", "[invalid", case_sensitive=False, is_regex=True)
        assert results == []

    def test_nested_quantifier_regex_rejected_before_search(self):
        content = "a" * 1000
        risky_pattern = "".join(["(", "a", "+", ")", "+", "$"])
        with pytest.raises(ValueError, match="nested quantifiers"):
            _validate_regex_pattern(risky_pattern)
        with pytest.raises(ValueError, match="nested quantifiers"):
            _search_content(content, "test.py", risky_pattern, case_sensitive=False, is_regex=True)

    def test_ambiguous_quantified_alternation_rejected_before_search(self):
        risky_pattern = "".join(["(", "a", "|", "a", ")", "+"])
        with pytest.raises(ValueError, match="nested quantifiers"):
            _validate_regex_pattern(risky_pattern)

    @pytest.mark.parametrize(
        "pattern",
        [
            r"(?:async\s+)?def",
            r"(\d+)?",
            r"(http|https)?",
            r"(?:foo|bar)+",
            r"(?:[a-z]+\d+)?",
        ],
    )
    def test_benign_grouped_regex_patterns_are_accepted(self, pattern):
        _validate_regex_pattern(pattern)

    def test_regex_pattern_length_limit(self):
        with pytest.raises(ValueError, match="exceeds"):
            _validate_regex_pattern("a" * 513)

    def test_special_regex_chars_escaped_in_literal_search(self):
        """When is_regex=False, special characters like . and + should be escaped."""
        content = "file.txt is here"
        results = _search_content(content, "test.py", "file.txt", case_sensitive=False, is_regex=False)
        assert len(results) == 1

        # "file.txt" as literal should NOT match "fileXtxt"
        content_no_dot = "fileXtxt is here"
        results = _search_content(content_no_dot, "test.py", "file.txt", case_sensitive=False, is_regex=False)
        assert len(results) == 0

    def test_parentheses_escaped_in_literal_search(self):
        """Parentheses should be treated as literal characters when is_regex=False."""
        content = "print(hello)"
        results = _search_content(content, "test.py", "print(hello)", case_sensitive=False, is_regex=False)
        assert len(results) == 1


class TestMultipleMatches:
    """Multiple match scenarios."""

    def test_matches_on_different_lines(self):
        content = "first match\nsecond line\nthird match"
        results = _search_content(content, "test.py", "match", case_sensitive=False, is_regex=False)
        assert len(results) == 2
        assert results[0].line == 1
        assert results[1].line == 3

    def test_multiple_matches_on_same_line(self):
        content = "foo bar foo baz foo"
        results = _search_content(content, "test.py", "foo", case_sensitive=False, is_regex=False)
        assert len(results) == 3
        assert results[0].column == 0
        assert results[1].column == 8
        assert results[2].column == 16
        # All should have the same line number
        assert all(r.line == 1 for r in results)


class TestContextLines:
    """Context before and after match lines."""

    def test_context_before_none_for_first_line(self):
        content = "first line\nsecond line\nthird line"
        results = _search_content(content, "test.py", "first", case_sensitive=False, is_regex=False)
        assert len(results) == 1
        assert results[0].context_before is None
        assert results[0].context_after == "second line"

    def test_context_after_none_for_last_line(self):
        content = "first line\nsecond line\nthird line"
        results = _search_content(content, "test.py", "third", case_sensitive=False, is_regex=False)
        assert len(results) == 1
        assert results[0].context_before == "second line"
        assert results[0].context_after is None

    def test_middle_line_has_both_contexts(self):
        content = "first line\nsecond line\nthird line"
        results = _search_content(content, "test.py", "second", case_sensitive=False, is_regex=False)
        assert len(results) == 1
        assert results[0].context_before == "first line"
        assert results[0].context_after == "third line"

    def test_two_line_file_first_line(self):
        content = "alpha\nbeta"
        results = _search_content(content, "test.py", "alpha", case_sensitive=False, is_regex=False)
        assert len(results) == 1
        assert results[0].context_before is None
        assert results[0].context_after == "beta"

    def test_two_line_file_second_line(self):
        content = "alpha\nbeta"
        results = _search_content(content, "test.py", "beta", case_sensitive=False, is_regex=False)
        assert len(results) == 1
        assert results[0].context_before == "alpha"
        assert results[0].context_after is None

    def test_single_line_file_no_context(self):
        content = "only line"
        results = _search_content(content, "test.py", "only", case_sensitive=False, is_regex=False)
        assert len(results) == 1
        assert results[0].context_before is None
        assert results[0].context_after is None
