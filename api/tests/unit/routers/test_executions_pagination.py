"""Keyset-cursor tests for the execution history endpoint.

History pagination must be keyset-based ("rows older than the last one I
saw"), not offset-based: on a busy instance new executions land constantly,
and an offset token re-serves the previous page's tail — users paginating
into the past see today's rows (and a "Today" header) on every page.
"""

from datetime import datetime, timezone
from uuid import uuid4


def test_cursor_round_trips_started_row() -> None:
    from src.routers.executions import (
        _decode_history_cursor,
        _encode_history_cursor,
    )

    started = datetime(2026, 7, 10, 12, 30, 45, 123456, tzinfo=timezone.utc)
    row_id = uuid4()

    token = _encode_history_cursor(started, row_id)
    decoded = _decode_history_cursor(token)

    assert decoded == (started, row_id)


def test_cursor_round_trips_null_started_row() -> None:
    """Scheduled/Pending rows have no started_at; the cursor must carry that."""
    from src.routers.executions import (
        _decode_history_cursor,
        _encode_history_cursor,
    )

    row_id = uuid4()
    token = _encode_history_cursor(None, row_id)

    assert _decode_history_cursor(token) == (None, row_id)


def test_cursor_is_opaque_not_a_bare_offset() -> None:
    """A numeric token is the legacy offset format, not a keyset cursor."""
    from src.routers.executions import _encode_history_cursor

    token = _encode_history_cursor(None, uuid4())
    assert not token.isdigit()


def test_decode_rejects_garbage_and_legacy_offsets() -> None:
    """Legacy numeric offsets and junk must decode to None (caller falls back)."""
    from src.routers.executions import _decode_history_cursor

    assert _decode_history_cursor("25") is None
    assert _decode_history_cursor("not-a-token") is None
    assert _decode_history_cursor("") is None
