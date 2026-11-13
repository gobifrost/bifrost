"""Log sanitization for user-controlled values.

Strips control chars and ANSI escape sequences from values that flow into
log statements, preventing log forgery (newline injection, terminal escape
injection) when the application's logger uses plain-text formatters.
"""

import logging
import re
import sys
import traceback
from typing import Any

_CTRL_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def log_safe(value: Any, max_len: int = 200) -> str:
    """Sanitize a value for safe inclusion in a log message.

    Replaces \\r and \\n with literal escapes, removes other ASCII control
    chars and ANSI escape sequences, and truncates to ``max_len`` characters.
    Use at log callsites whose interpolated value comes from user input
    (HTTP body, URL param, webhook payload, third-party API response).
    """
    s = str(value).replace("\r", "\\r").replace("\n", "\\n")
    s = _CTRL_RE.sub("", s)
    return s if len(s) <= max_len else s[:max_len] + "..."


def log_safe_exception(
    logger: logging.Logger,
    message: str,
    *args: Any,
) -> None:
    """Log the current exception with safe detail and useful frame locations.

    Frame source lines are intentionally omitted: they can contain credentials.
    Exception messages, notes and chains are escaped and bounded independently
    from the trusted traceback structure.
    """
    if not logger.isEnabledFor(logging.ERROR):
        return
    error = sys.exception()
    lines: list[str] = []
    seen: set[int] = set()

    def append_exception(exc: BaseException) -> None:
        if id(exc) in seen:
            return
        seen.add(id(exc))
        cause = exc.__cause__
        context = exc.__context__
        if cause is not None:
            append_exception(cause)
            lines.append("The above exception was the direct cause of the following exception:")
        elif context is not None and not exc.__suppress_context__:
            append_exception(context)
            lines.append("During handling of the above exception, another exception occurred:")
        lines.append("Traceback (most recent call last):")
        for frame in traceback.extract_tb(exc.__traceback__):
            lines.append(
                f'  File "{log_safe(frame.filename)}", line {frame.lineno}, in {log_safe(frame.name)}'
            )
        lines.append(f"{log_safe(type(exc).__name__)}: {log_safe(exc)}")
        for note in getattr(exc, "__notes__", ()):
            lines.append(log_safe(note))
        if isinstance(exc, BaseExceptionGroup):
            for child in exc.exceptions:
                append_exception(child)

    if error is not None:
        append_exception(error)
    logger.error("%s\n%s", message % args if args else message, "\n".join(lines))
