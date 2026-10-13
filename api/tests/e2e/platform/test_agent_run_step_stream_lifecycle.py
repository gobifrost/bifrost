"""Redis lifecycle coverage for transient agent-run step streams."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.core.cache.keys import agent_run_steps_stream_key
from src.core.cache.redis_client import get_redis
from src.models.orm import AgentRun
from src.services.agent_run_step_streams import (
    AGENT_RUN_STEP_STREAM_TTL_SECONDS,
    append_agent_run_step,
    inspect_orphaned_agent_run_step_streams,
)
from src.services.run_retention.deleter import delete_agent_run_batch

pytestmark = pytest.mark.e2e


def _stream_entry(run_id: str) -> dict[str, str]:
    return {
        "id": str(uuid4()),
        "run_id": run_id,
        "step_number": "1",
        "type": "llm_request",
        "content": "{}",
        "tokens_used": "",
        "duration_ms": "",
        "created_at": datetime.now(UTC).isoformat(),
    }


async def test_step_append_sets_a_sliding_ttl_with_the_stream_entry():
    run_id = uuid4()
    stream_key = agent_run_steps_stream_key(str(run_id))

    async with get_redis() as redis:
        await redis.delete(stream_key)
        try:
            await append_agent_run_step(redis, run_id=run_id, fields=_stream_entry(str(run_id)))

            entries = await redis.xrange(stream_key, min="-", max="+")
            ttl = await redis.ttl(stream_key)

            assert len(entries) == 1
            assert entries[0][1]["run_id"] == str(run_id)
            assert 0 < ttl <= AGENT_RUN_STEP_STREAM_TTL_SECONDS

            await redis.expire(stream_key, 1)
            await append_agent_run_step(redis, run_id=run_id, fields=_stream_entry(str(run_id)))

            assert await redis.ttl(stream_key) > 1
        finally:
            await redis.delete(stream_key)


async def test_orphan_inspection_uses_agent_run_rows_and_never_deletes(db_session):
    now = datetime.now(UTC)
    existing_run_id = uuid4()
    missing_old_run_id = uuid4()
    missing_recent_run_id = uuid4()
    stream_keys = [
        agent_run_steps_stream_key(str(run_id))
        for run_id in (existing_run_id, missing_old_run_id, missing_recent_run_id)
    ]
    unrelated_prefix = f"test:agent-step-orphan-inspection:{uuid4()}:"
    unrelated_keys = [f"{unrelated_prefix}{index}" for index in range(1000)]
    db_session.add(AgentRun(id=existing_run_id, trigger_type="test", status="running"))
    await db_session.commit()

    old_entry_id = f"{int((now - timedelta(seconds=AGENT_RUN_STEP_STREAM_TTL_SECONDS + 1)).timestamp() * 1000)}-0"
    try:
        async with get_redis() as redis:
            async with redis.pipeline(transaction=False) as pipeline:
                for key in unrelated_keys:
                    pipeline.set(key, "unrelated", ex=60)
                await pipeline.execute()
            for run_id in (existing_run_id, missing_old_run_id):
                await redis.xadd(
                    agent_run_steps_stream_key(str(run_id)),
                    _stream_entry(str(run_id)),
                    id=old_entry_id,
                )
            await redis.xadd(
                agent_run_steps_stream_key(str(missing_recent_run_id)),
                _stream_entry(str(missing_recent_run_id)),
            )

            # SCAN MATCH filters each page; an empty page does not end traversal.
            totals = {
                "scanned": 0,
                "protected_existing_runs": 0,
                "recent_missing_runs": 0,
                "orphaned_streams": 0,
            }
            cursor = 0
            saw_empty_page = False
            while True:
                report = await inspect_orphaned_agent_run_step_streams(
                    db_session,
                    redis,
                    cursor=cursor,
                    limit=100,
                    now=now,
                )
                assert report["scanned"] <= 100
                assert report["uninspected_keys"] == 0
                saw_empty_page |= report["scanned"] == 0 and report["next_cursor"] != 0
                for category in totals:
                    totals[category] += report[category]
                cursor = report["next_cursor"]
                if cursor == 0:
                    break

            assert saw_empty_page
            assert totals["scanned"] >= 3
            assert totals["protected_existing_runs"] >= 1
            assert totals["recent_missing_runs"] >= 1
            assert totals["orphaned_streams"] >= 1
            for stream_key in stream_keys:
                assert await redis.exists(stream_key)
    finally:
        async with get_redis() as redis:
            await redis.delete(*stream_keys, *unrelated_keys)
        await db_session.execute(delete(AgentRun).where(AgentRun.id == existing_run_id))
        await db_session.commit()


async def test_retention_deletes_the_stream_for_a_deleted_agent_run(
    db_session,
    run_retention_lease,
):
    run_id = uuid4()
    stream_key = agent_run_steps_stream_key(str(run_id))
    db_session.add(
        AgentRun(
            id=run_id,
            trigger_type="test",
            status="completed",
            completed_at=datetime(1990, 1, 1, tzinfo=UTC),
        )
    )
    await db_session.commit()

    try:
        async with get_redis() as redis:
            await redis.xadd(stream_key, _stream_entry(str(run_id)))

        deleted = await delete_agent_run_batch(
            cutoff=datetime(2000, 1, 1, tzinfo=UTC),
            job_id=run_retention_lease.job_id,
            lease_token=run_retention_lease.token,
            limit=1,
        )

        assert deleted == 1
        async with get_redis() as redis:
            assert await redis.exists(stream_key) == 0
    finally:
        async with get_redis() as redis:
            await redis.delete(stream_key)
        await db_session.execute(delete(AgentRun).where(AgentRun.id == run_id))
        await db_session.commit()
