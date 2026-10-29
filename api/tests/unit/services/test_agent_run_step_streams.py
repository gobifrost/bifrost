"""Unit contracts for agent-run step stream lifecycle operations."""

from uuid import uuid4

import pytest
from redis.exceptions import ResponseError

from src.core.cache.keys import agent_run_steps_stream_key
from src.services.agent_run_step_streams import (
    AGENT_RUN_STEP_STREAM_MAXLEN,
    AGENT_RUN_STEP_STREAM_TTL_SECONDS,
    append_agent_run_step,
)


class _Pipeline:
    def __init__(self) -> None:
        self.xadd_calls: list[tuple] = []
        self.expire_calls: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    def xadd(self, *args, **kwargs) -> None:
        self.xadd_calls.append((args, kwargs))

    def expire(self, *args, **kwargs) -> None:
        self.expire_calls.append((args, kwargs))

    async def execute(self):
        return ["123-0", True]


class _Redis:
    def __init__(self) -> None:
        self.pipeline_instance = _Pipeline()
        self.transaction: bool | None = None

    def pipeline(self, *, transaction: bool):
        self.transaction = transaction
        return self.pipeline_instance


async def test_append_uses_one_transaction_for_xadd_and_expiry():
    redis = _Redis()
    run_id = uuid4()
    fields = {"id": str(uuid4()), "run_id": str(run_id)}

    entry_id = await append_agent_run_step(redis, run_id=run_id, fields=fields)  # type: ignore[arg-type]

    assert entry_id == "123-0"
    assert redis.transaction is True
    assert redis.pipeline_instance.xadd_calls == [
        ((f"bifrost:agent_run_steps:{run_id}", fields), {"maxlen": AGENT_RUN_STEP_STREAM_MAXLEN})
    ]
    assert redis.pipeline_instance.expire_calls == [
        ((f"bifrost:agent_run_steps:{run_id}", AGENT_RUN_STEP_STREAM_TTL_SECONDS), {})
    ]


class _ScalarResult:
    def all(self) -> list:
        return []


class _Session:
    async def scalars(self, statement):
        return _ScalarResult()


class _InspectorRedis:
    def __init__(self, keys: list[str]) -> None:
        self.keys = keys
        self.inspected_keys: list[str] = []

    async def scan(self, cursor: int, *, match: str, count: int):
        return 9, self.keys

    async def xrevrange(self, key: str, *, max: str, min: str, count: int):
        self.inspected_keys.append(key)
        if len(self.inspected_keys) == 1:
            raise ResponseError("WRONGTYPE")
        return []


class _HugeEntryRedis:
    def __init__(self, key: str) -> None:
        self.key = key

    async def scan(self, cursor: int, *, match: str, count: int):
        return 0, [self.key]

    async def xrevrange(self, key: str, *, max: str, min: str, count: int):
        return [("999999999999999999999999999999-0", {})]


async def test_orphan_inspection_bounds_a_scan_count_hint_and_classifies_wrong_types():
    keys = [agent_run_steps_stream_key(str(uuid4())) for _ in range(2)]
    redis = _InspectorRedis(keys)

    from src.services.agent_run_step_streams import inspect_orphaned_agent_run_step_streams

    report = await inspect_orphaned_agent_run_step_streams(
        _Session(),  # type: ignore[arg-type]
        redis,  # type: ignore[arg-type]
        limit=1,
    )

    assert report["next_cursor"] == 9
    assert report["scanned"] == 1
    assert report["uninspected_keys"] == 1
    assert report["non_stream_keys"] == 1
    assert redis.inspected_keys == keys[:1]


async def test_orphan_inspection_protects_an_unparseable_stream_timestamp():
    from src.services.agent_run_step_streams import inspect_orphaned_agent_run_step_streams

    redis = _HugeEntryRedis(agent_run_steps_stream_key(str(uuid4())))

    report = await inspect_orphaned_agent_run_step_streams(
        _Session(),  # type: ignore[arg-type]
        redis,  # type: ignore[arg-type]
    )

    assert report["orphaned_streams"] == 0
    assert report["unparseable_newest_entry"] == 1


async def test_orphan_inspection_rejects_an_unbounded_page_size():
    from src.services.agent_run_step_streams import inspect_orphaned_agent_run_step_streams

    with pytest.raises(ValueError, match="between 1 and"):
        await inspect_orphaned_agent_run_step_streams(
            _Session(),  # type: ignore[arg-type]
            _InspectorRedis([]),  # type: ignore[arg-type]
            limit=101,
        )
