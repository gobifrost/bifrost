"""Unit tests for the workflow-operation-usage Redis-to-Postgres flush job."""

from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.models.orm.workflow_operation_usage import WorkflowOperationUsage
from src.models.orm.workflows import Workflow

PATH_GET_REDIS = "src.jobs.schedulers.workflow_operation_usage_flush._get_redis"
PATH_DB_CTX = "src.jobs.schedulers.workflow_operation_usage_flush.get_db_context"


class _FakeRedis:
    """In-memory stand-in exercising the real rename/hgetall/delete drain path."""

    def __init__(self, data: dict[str, dict[str, str]]):
        self._data = data

    async def rename(self, src: str, dst: str) -> None:
        import redis.asyncio as aioredis

        if src not in self._data:
            raise aioredis.ResponseError("ERR no such key")
        self._data[dst] = self._data.pop(src)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._data.get(key, {}))

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)


class _DbCtx:
    """Runs the flush job against the test's own session (see
    test_deferred_execution_promoter.py for the same pattern)."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_args):
        return False


def _today_key() -> str:
    today = datetime.now(timezone.utc).date().isoformat()
    return f"bifrost:wf_usage:{today}"


@pytest.mark.asyncio
async def test_flush_upserts_counts_for_known_workflows(db_session):
    from src.jobs.schedulers.workflow_operation_usage_flush import (
        flush_workflow_operation_usage,
    )

    workflow = Workflow(
        id=uuid4(),
        name="demo_workflow",
        function_name="demo_workflow",
        type="workflow",
        path="workflows/demo_workflow.py",
    )
    db_session.add(workflow)
    await db_session.commit()

    redis_data = {
        _today_key(): {
            f"{workflow.id}|config.get": "3",
            f"{workflow.id}|GET /api/sdk/tables/{{table_id}}": "2",
        }
    }
    fake_redis = _FakeRedis(redis_data)

    with (
        patch(PATH_GET_REDIS, return_value=fake_redis),
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
    ):
        upserted = await flush_workflow_operation_usage()

    assert upserted == 2

    rows = (
        await db_session.execute(
            select(WorkflowOperationUsage).where(
                WorkflowOperationUsage.workflow_id == workflow.id
            )
        )
    ).scalars().all()
    by_key = {row.operation_key: row.count for row in rows}
    assert by_key["config.get"] == 3
    assert by_key["GET /api/sdk/tables/{table_id}"] == 2

    # The hash was drained (renamed away and deleted) — nothing left to redrain.
    assert _today_key() not in redis_data


@pytest.mark.asyncio
async def test_flush_is_idempotent_across_two_calls(db_session):
    from src.jobs.schedulers.workflow_operation_usage_flush import (
        flush_workflow_operation_usage,
    )

    workflow = Workflow(
        id=uuid4(),
        name="demo_workflow_2",
        function_name="demo_workflow_2",
        type="workflow",
        path="workflows/demo_workflow_2.py",
    )
    db_session.add(workflow)
    await db_session.commit()

    redis_data = {_today_key(): {f"{workflow.id}|agents.list": "5"}}
    fake_redis = _FakeRedis(redis_data)

    with (
        patch(PATH_GET_REDIS, return_value=fake_redis),
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
    ):
        first = await flush_workflow_operation_usage()
        # Nothing new buffered since the first drain emptied the hash.
        second = await flush_workflow_operation_usage()

    assert first == 1
    assert second == 0

    row = (
        await db_session.execute(
            select(WorkflowOperationUsage).where(
                WorkflowOperationUsage.workflow_id == workflow.id,
                WorkflowOperationUsage.operation_key == "agents.list",
            )
        )
    ).scalar_one()
    assert row.count == 5


@pytest.mark.asyncio
async def test_flush_drops_rows_for_deleted_workflows(db_session):
    from src.jobs.schedulers.workflow_operation_usage_flush import (
        flush_workflow_operation_usage,
    )

    missing_workflow_id = uuid4()
    redis_data = {_today_key(): {f"{missing_workflow_id}|agents.list": "1"}}
    fake_redis = _FakeRedis(redis_data)

    with (
        patch(PATH_GET_REDIS, return_value=fake_redis),
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
    ):
        upserted = await flush_workflow_operation_usage()

    assert upserted == 0

    rows = (
        await db_session.execute(
            select(WorkflowOperationUsage).where(
                WorkflowOperationUsage.workflow_id == missing_workflow_id
            )
        )
    ).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_flush_with_no_buffered_data_is_a_noop(db_session):
    from src.jobs.schedulers.workflow_operation_usage_flush import (
        flush_workflow_operation_usage,
    )

    fake_redis = _FakeRedis({})

    with (
        patch(PATH_GET_REDIS, return_value=fake_redis),
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
    ):
        upserted = await flush_workflow_operation_usage()

    assert upserted == 0
