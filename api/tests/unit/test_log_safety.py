"""Tests for src.core.log_safety.log_safe."""

from src.core.log_safety import log_safe


def test_plain_string_passes_through():
    assert log_safe("hello world") == "hello world"


def test_empty_string():
    assert log_safe("") == ""


def test_newline_replaced_with_literal_escape():
    assert log_safe("line1\nline2") == "line1\\nline2"


def test_carriage_return_replaced_with_literal_escape():
    assert log_safe("line1\rline2") == "line1\\rline2"


def test_crlf_replaced_with_literal_escapes():
    assert log_safe("line1\r\nline2") == "line1\\r\\nline2"


def test_null_byte_stripped():
    assert log_safe("foo\x00bar") == "foobar"


def test_other_control_chars_stripped():
    # \x01-\x08, \x0b, \x0c, \x0e-\x1f, \x7f
    assert log_safe("a\x01b\x02c\x07d\x7fe") == "abcde"


def test_tab_preserved():
    # Tab (\x09) is not in the stripped range — it's a whitespace char that
    # doesn't break log lines, so keep it for readability.
    assert log_safe("col1\tcol2") == "col1\tcol2"


def test_ansi_color_escape_stripped():
    # \x1b[31m red \x1b[0m
    assert log_safe("\x1b[31mred\x1b[0m") == "red"


def test_ansi_cursor_escape_stripped():
    # CSI cursor up
    assert log_safe("before\x1b[2Aafter") == "beforeafter"


def test_truncation_at_max_len():
    s = "x" * 250
    result = log_safe(s, max_len=200)
    assert len(result) == 203  # 200 chars + "..."
    assert result.endswith("...")
    assert result[:200] == "x" * 200


def test_no_truncation_when_below_limit():
    s = "x" * 50
    assert log_safe(s, max_len=200) == s


def test_truncation_exact_boundary():
    s = "x" * 200
    assert log_safe(s, max_len=200) == s


def test_int_input_handled():
    assert log_safe(42) == "42"


def test_none_input_handled():
    assert log_safe(None) == "None"


def test_dict_input_handled():
    result = log_safe({"key": "value"})
    assert result == "{'key': 'value'}"


def test_list_input_handled():
    assert log_safe([1, 2, 3]) == "[1, 2, 3]"


def test_log_forgery_attempt_neutralized():
    # Classic log injection: attacker tries to inject a fake log line.
    payload = "user123\n2026-01-01 INFO root: admin granted access"
    result = log_safe(payload)
    assert "\n" not in result
    assert result.startswith("user123\\n")


def test_combined_control_and_ansi():
    payload = "before\x1b[31m\x00\nafter"
    result = log_safe(payload)
    assert result == "before\\nafter"


def test_custom_max_len():
    assert log_safe("abcdefghij", max_len=5) == "abcde..."


def test_safe_exception_preserves_frames_and_sanitizes_chained_details(caplog):
    import logging

    from src.core.log_safety import log_safe_exception

    logger = logging.getLogger(__name__)
    payload = "remote\nFORGED\r\x1b[31m\x00" + "x" * 500
    try:
        try:
            raise ValueError(payload)
        except ValueError as cause:
            raise RuntimeError(payload) from cause
    except RuntimeError:
        log_safe_exception(logger, "Operation %s failed", "test")

    output = caplog.text
    assert "Operation test failed" in output
    assert "test_safe_exception_preserves_frames_and_sanitizes_chained_details" in output
    assert 'File "' in output
    assert "direct cause" in output
    assert "ValueError: " + log_safe(payload) in output
    assert "RuntimeError: " + log_safe(payload) in output
    assert "\nFORGED" not in output
    assert "\x1b" not in output
    assert "\x00" not in output
    assert "x" * 201 not in output

