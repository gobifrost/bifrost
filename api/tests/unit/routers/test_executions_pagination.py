"""Keyset-cursor tests for the execution history endpoint.

History pagination must be keyset-based ("rows older than the last one I
saw"), not offset-based: on a busy instance new executions land constantly,
and an offset token re-serves the previous page's tail — users paginating
into the past see today's rows (and a "Today" header) on every page.
"""

import base64
from datetime import datetime, timezone
import json
from uuid import uuid4

import pytest


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


@pytest.mark.parametrize("started_at", [None, "2026-07-10T12:30:45+00:00"])
def test_legacy_started_at_cursor_restarts(started_at) -> None:
    """A v1 started_at position cannot be reused against the display timeline."""
    from shared.sdk_execution_reads import decode_history_cursor

    row_id = uuid4()
    token = base64.urlsafe_b64encode(json.dumps({
        "v": 1, "s": started_at, "i": str(row_id),
    }).encode()).decode()

    assert decode_history_cursor(token) == (None, row_id)


@pytest.mark.parametrize("started_at", ["", "not-a-date", 123])
def test_legacy_restart_requires_valid_timestamp(started_at) -> None:
    from shared.sdk_execution_reads import decode_history_cursor

    token = base64.urlsafe_b64encode(json.dumps({
        "v": 1, "s": started_at, "i": str(uuid4()),
    }).encode()).decode()

    assert decode_history_cursor(token) is None


def test_cursor_is_opaque_not_a_bare_offset() -> None:
    """A numeric token is the legacy offset format, not a keyset cursor."""
    from shared.sdk_execution_reads import encode_history_cursor

    token = encode_history_cursor(None, uuid4())
    assert not token.isdigit()
    assert json.loads(base64.urlsafe_b64decode(token))["v"] == 2


def test_decode_rejects_garbage_and_legacy_offsets() -> None:
    """Legacy numeric offsets and junk must decode to None (caller falls back)."""
    from shared.sdk_execution_reads import decode_history_cursor

    assert decode_history_cursor("25") is None
    assert decode_history_cursor("not-a-token") is None
    assert decode_history_cursor("") is None
