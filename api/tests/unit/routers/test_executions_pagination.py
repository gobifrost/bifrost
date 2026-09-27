"""Keyset-cursor tests for the execution history endpoint.

History pagination must be keyset-based ("rows older than the last one I
saw"), not offset-based: on a busy instance new executions land constantly,
and an offset token re-serves the previous page's tail — users paginating
into the past see today's rows (and a "Today" header) on every page.
"""

from datetime import datetime, timezone
from uuid import uuid4


def test_cursor_round_trips_timeline_timestamp() -> None:
    from shared.sdk_execution_reads import (
        decode_history_cursor,
        encode_history_cursor,
    )

    timeline_at = datetime(2026, 7, 10, 12, 30, 45, 123456, tzinfo=timezone.utc)
    row_id = uuid4()

    token = encode_history_cursor(timeline_at, row_id)
    decoded = decode_history_cursor(token)

    assert decoded == (timeline_at, row_id)


def test_cursor_round_trips_legacy_null_timestamp() -> None:
    """Cursors minted before timeline anchors may still carry a null value."""
    from shared.sdk_execution_reads import (
        decode_history_cursor,
        encode_history_cursor,
    )

    row_id = uuid4()
    token = encode_history_cursor(None, row_id)

    assert decode_history_cursor(token) == (None, row_id)


def test_cursor_is_opaque_not_a_bare_offset() -> None:
    """A numeric token is the legacy offset format, not a keyset cursor."""
    from shared.sdk_execution_reads import encode_history_cursor

    token = encode_history_cursor(None, uuid4())
    assert not token.isdigit()


def test_decode_rejects_garbage_and_legacy_offsets() -> None:
    """Legacy numeric offsets and junk must decode to None (caller falls back)."""
    from shared.sdk_execution_reads import decode_history_cursor

    assert decode_history_cursor("25") is None
    assert decode_history_cursor("not-a-token") is None
    assert decode_history_cursor("") is None
