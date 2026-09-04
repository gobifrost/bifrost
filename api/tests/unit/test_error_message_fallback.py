"""Regression tests for exception message fallback formatting."""

import httpx

from src.core.error_messages import format_exception_message


def test_format_exception_message_uses_class_name_for_empty_message() -> None:
    exc = httpx.ReadError(
        "",
        request=httpx.Request("GET", "https://example.com"),
    )

    assert format_exception_message(exc, context="executing workflow") == (
        "ReadError while executing workflow"
    )


def test_format_exception_message_preserves_non_empty_message() -> None:
    exc = RuntimeError("boom")

    assert format_exception_message(exc, context="executing workflow") == "boom"
