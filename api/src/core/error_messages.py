"""Helpers for consistent exception-to-message formatting."""

from __future__ import annotations


def format_exception_message(exc: BaseException, *, context: str | None = None) -> str:
    """Return a stable, useful message even when ``str(exc)`` is empty."""

    message = str(exc).strip()
    if message:
        return message

    exc_name = type(exc).__name__
    if context:
        return f"{exc_name} while {context}"
    return exc_name
