"""Trailing service-log persistence: stream drain, cursor resume, trim, reads.

Uses the real test-stack PostgreSQL (rollback-isolated db_session) with a
stream-capable faked Redis. Covers bounded flush batches, cursor resume
across owner failover (no duplicates), poison-entry skipping, the trailing
retention cap, and the read endpoint's filter contract.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from src.models.orm.services import ServiceAttempt, ServiceLog
from src.services import service_lifecycle, service_log_flush


class FakeStreamRedis:
    """get/setex/delete plus xadd/xrange streams (minimal)."""

    def __init__(self):
        self.values = {}
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self._seq = 0

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.values[key] = value

    async def set(self, key, value):
        self.values[key] = value

    async def delete(self, *keys):
        for key in keys:
            self.values.pop(key, None)

    async def xadd(self, key, fields, maxlen=None):
        self._seq += 1
        entry_id = f"{self._seq}-0"
        self.streams.setdefault(key, []).append((entry_id, dict(fields)))
        if maxlen is not None:
            self.streams[key] = self.streams[key][-maxlen:]
        return entry_id

    async def xrange(self, key, min="-", max="+", count=None):
        entries = list(self.streams.get(key, []))
        if min != "-":
            assert min.startswith("("), min
            entries = [
                e for e in entries if _stream_id(e[0]) > _stream_id(min[1:])
            ]
        if count is not None:
            entries = entries[:count]
        return entries


def _stream_id(entry_id: str) -> tuple[int, int]:
    major, _, minor = entry_id.partition("-")
    return int(major), int(minor or 0)


@asynccontextmanager
async def _fake_redis_holder(holder):
    yield holder["redis"]


@pytest.fixture
def redis_holder():
    return {"redis": FakeStreamRedis()}


@pytest.fixture(autouse=True)
def _patch_redis(redis_holder):
    with patch(
        "src.core.cache.get_redis",
        side_effect=lambda: _fake_redis_holder(redis_holder),
    ):
        yield


async def _ensure_service(db_session):
    from src.models.orm.organizations import Organization
    from src.models.orm.workflows import Workflow

    org = Organization(
        id=uuid4(), name=f"acme_{uuid4().hex[:8]}", created_by="tester"
    )
    db_session.add(org)
    await db_session.flush()
    suffix = uuid4().hex[:8]
    wf = Workflow(
        id=uuid4(),
        name=f"svc_{suffix}",
        function_name=f"svc_{suffix}",
        path=f"workflows/svc_{suffix}.py",
        type="service",
        organization_id=org.id,
        is_active=True,
    )
    db_session.add(wf)
    await db_session.flush()
    definition = await service_lifecycle.ensure_definition_for_workflow(
        db_session, wf, created_by="tester"
    )
    await db_session.flush()
    return definition


async def _attempt(db_session, definition, **overrides):
    now = datetime.now(timezone.utc)
    overrides.setdefault("state", "running")
    attempt = ServiceAttempt(
        id=uuid4(),
        service_id=definition.id,
        lease_token="tok",
        lease_expires_at=now + timedelta(seconds=60),
        **overrides,
    )
    db_session.add(attempt)
    await db_session.flush()
    return attempt


def _log_line(redis, attempt_id, message, level="INFO", ts=None):
    from src.core.cache.keys import service_logs_stream_key

    return redis.xadd(
        service_logs_stream_key(str(attempt_id)),
        {
            "service_id": "svc",
            "attempt_id": str(attempt_id),
            "level": level,
            "message": message,
            "timestamp": (ts or datetime.now(timezone.utc)).isoformat(),
        },
    )


@pytest.mark.asyncio
async def test_flush_drains_stream_and_resumes_from_cursor(
    db_session, redis_holder
):
    """Beat flush stages rows; the cursor advances only post-commit.

    The second beat re-reads nothing once the cursor is stored — and
    replays cleanly (no gaps) when the first tick never commits.
    """
    from src.core.cache.keys import service_logs_cursor_key

    definition = await _ensure_service(db_session)
    attempt = await _attempt(db_session, definition)
    redis = redis_holder["redis"]
    await _log_line(redis, attempt.id, "hello")
    await _log_line(redis, attempt.id, "world", level="ERROR")

    count, last_id = await service_log_flush.flush_attempt_logs(
        db_session, definition.id, attempt.id
    )
    assert (count, last_id) == (2, "2-0")
    # Staged but uncommitted: the cursor is untouched until store.
    assert service_logs_cursor_key(str(attempt.id)) not in redis.values
    rows, total, _ = await service_lifecycle.list_service_logs(
        db_session, definition.id
    )
    assert total == 2
    assert [r.message for r in rows] == ["hello", "world"]
    assert [r.level for r in rows] == ["INFO", "ERROR"]
    assert all(r.attempt_id == attempt.id for r in rows)

    # Post-commit cursor store: the next beat only reads new entries.
    # (No commit here — db_session rollback owns cleanup; the cursor
    # mechanics are identical.)
    assert (
        await service_log_flush.store_log_cursor(attempt.id, last_id)
        is True
    )
    assert redis.values[service_logs_cursor_key(str(attempt.id))] == "2-0"
    await _log_line(redis, attempt.id, "again")
    count, last_id = await service_log_flush.flush_attempt_logs(
        db_session, definition.id, attempt.id
    )
    assert (count, last_id) == (1, "3-0")
    _, total, _ = await service_lifecycle.list_service_logs(
        db_session, definition.id
    )
    assert total == 3


@pytest.mark.asyncio
async def test_rollback_replays_without_gap(db_session, redis_holder):
    """A failed tick commit replays the same tail (no cursor, no loss)."""
    definition = await _ensure_service(db_session)
    attempt = await _attempt(db_session, definition)
    redis = redis_holder["redis"]
    await _log_line(redis, attempt.id, "hello")

    savepoint = await db_session.begin_nested()
    count, _ = await service_log_flush.flush_attempt_logs(
        db_session, definition.id, attempt.id
    )
    assert count == 1
    await savepoint.rollback()

    # Cursor never advanced: the retry re-reads and re-inserts cleanly.
    count, last_id = await service_log_flush.flush_attempt_logs(
        db_session, definition.id, attempt.id
    )
    assert (count, last_id) == (1, "1-0")
    _, total, _ = await service_lifecycle.list_service_logs(
        db_session, definition.id
    )
    assert total == 1


@pytest.mark.asyncio
async def test_flush_batch_bound_and_poison_skip(db_session, redis_holder):
    """Batches bound one beat's work; unparsable rows are skipped, not stuck."""
    definition = await _ensure_service(db_session)
    attempt = await _attempt(db_session, definition)
    redis = redis_holder["redis"]
    await _log_line(redis, attempt.id, "one")
    await redis.xadd("bifrost:service-logs:" + str(attempt.id), {"junk": 1})
    await _log_line(redis, attempt.id, "three")

    # Bounded batches pair with post-commit cursor stores (the loop
    # protocol): poison rows are skipped without stalling the cursor.
    count, last_id = await service_log_flush.flush_attempt_logs(
        db_session, definition.id, attempt.id, batch=2
    )
    assert (count, last_id) == (1, "2-0")
    assert last_id is not None
    await service_log_flush.store_log_cursor(attempt.id, last_id)
    count, last_id = await service_log_flush.flush_attempt_logs(
        db_session, definition.id, attempt.id, batch=2
    )
    assert (count, last_id) == (1, "3-0")
    rows, _, _ = await service_lifecycle.list_service_logs(
        db_session, definition.id
    )
    assert [r.message for r in rows] == ["one", "three"]


@pytest.mark.asyncio
async def test_flush_never_raises(db_session, redis_holder):
    """Redis outage during a beat yields 0, not a supervision failure."""
    definition = await _ensure_service(db_session)
    attempt = await _attempt(db_session, definition)

    class _Boom:
        def __getattr__(self, name):
            raise RuntimeError("redis down")

    redis_holder["redis"] = _Boom()
    assert await service_log_flush.flush_attempt_logs(
        db_session, definition.id, attempt.id
    ) == (0, None)
    assert (
        await service_log_flush.store_log_cursor(attempt.id, "1-0")
        is False
    )


@pytest.mark.asyncio
async def test_claim_loop_stages_and_stores_cursors(db_session, redis_holder):
    """_flush_logs stages; _store_cursors persists post-commit."""
    from src.core.cache.keys import service_logs_cursor_key
    from src.services.service_claim import OwnedAttempt, ServiceClaimLoop

    definition = await _ensure_service(db_session)
    attempt = await _attempt(db_session, definition)
    redis = redis_holder["redis"]
    await _log_line(redis, attempt.id, "hello")

    loop = ServiceClaimLoop(worker_id="worker-1", pool=None)
    owned = OwnedAttempt(
        attempt_id=attempt.id,
        service_id=definition.id,
        lease_token="tok",
    )
    await loop._flush_logs(db_session, owned)
    assert loop._pending_cursors == {str(attempt.id): "1-0"}
    assert service_logs_cursor_key(str(attempt.id)) not in redis.values

    await loop._store_cursors()
    assert loop._pending_cursors == {}
    assert redis.values[service_logs_cursor_key(str(attempt.id))] == "1-0"


@pytest.mark.asyncio
async def test_trim_keeps_newest_rows(db_session):
    """Retention trims beyond the trailing cap, newest wins."""
    definition = await _ensure_service(db_session)
    attempt = await _attempt(db_session, definition)
    base = datetime.now(timezone.utc)
    for i in range(5):
        db_session.add(
            ServiceLog(
                service_id=definition.id,
                attempt_id=attempt.id,
                level="INFO",
                message=f"line-{i}",
                timestamp=base + timedelta(seconds=i),
            )
        )
    await db_session.flush()

    assert (
        await service_log_flush.trim_service_logs(
            db_session, definition.id, retain=3
        )
        == 2
    )
    assert (
        await service_log_flush.count_service_logs(db_session, definition.id)
        == 3
    )
    rows, _, _ = await service_lifecycle.list_service_logs(
        db_session, definition.id
    )
    assert [r.message for r in rows] == ["line-2", "line-3", "line-4"]


@pytest.mark.asyncio
async def test_list_filters_attempt_levels_and_dates(db_session):
    """Read contract: attempt scope, level allowlist, window, total."""
    definition = await _ensure_service(db_session)
    attempt_a = await _attempt(db_session, definition)
    attempt_b = await _attempt(db_session, definition, state="stopped")
    base = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    seed = [
        (attempt_a, "INFO", "start", base),
        (attempt_a, "ERROR", "boom", base + timedelta(minutes=1)),
        (attempt_b, "INFO", "other", base + timedelta(minutes=2)),
        (attempt_a, "DEBUG", "quiet", base + timedelta(minutes=3)),
    ]
    for attempt, level, message, ts in seed:
        db_session.add(
            ServiceLog(
                service_id=definition.id,
                attempt_id=attempt.id,
                level=level,
                message=message,
                timestamp=ts,
            )
        )
    await db_session.flush()

    rows, total, _ = await service_lifecycle.list_service_logs(
        db_session, definition.id, attempt_id=attempt_a.id
    )
    assert total == 3
    assert all(r.attempt_id == attempt_a.id for r in rows)

    rows, total, _ = await service_lifecycle.list_service_logs(
        db_session, definition.id, levels=["error"]
    )
    assert total == 1
    assert rows[0].message == "boom"

    rows, total, _ = await service_lifecycle.list_service_logs(
        db_session,
        definition.id,
        start=base + timedelta(minutes=1),
        end=base + timedelta(minutes=2),
    )
    assert total == 2
    # Chronological (timestamp, id).
    assert [r.message for r in rows] == ["boom", "other"]

    # Attempt rows carry UUIDs (not strings) for the response DTO.
    assert isinstance(rows[0].attempt_id, UUID)
    assert isinstance(rows[0].service_id, UUID)


@pytest.mark.asyncio
async def test_list_newest_first_pages_by_continuation_token(db_session):
    """Keyset paging: newest page first, token reaches the tail stably."""
    definition = await _ensure_service(db_session)
    attempt = await _attempt(db_session, definition)
    base = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)
    for i in range(5):
        db_session.add(
            ServiceLog(
                service_id=definition.id,
                attempt_id=attempt.id,
                level="INFO",
                message=f"line-{i}",
                timestamp=base + timedelta(seconds=i),
            )
        )
    await db_session.flush()

    from src.repositories.execution_logs import decode_execution_log_cursor

    rows, total, token = await service_lifecycle.list_service_logs(
        db_session, definition.id, newest_first=True, limit=2
    )
    assert total == 5
    assert [r.message for r in rows] == ["line-4", "line-3"]
    assert token is not None

    rows, total, token = await service_lifecycle.list_service_logs(
        db_session,
        definition.id,
        newest_first=True,
        limit=2,
        cursor=decode_execution_log_cursor(token),
    )
    assert total == 5
    assert [r.message for r in rows] == ["line-2", "line-1"]
    assert token is not None

    rows, total, token = await service_lifecycle.list_service_logs(
        db_session,
        definition.id,
        newest_first=True,
        limit=2,
        cursor=decode_execution_log_cursor(token),
    )
    assert [r.message for r in rows] == ["line-0"]
    assert token is None
