"""Real-Postgres coverage for daily pseudonymous usage reporting."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete

import shared.usage_reporting as usage_reporting
from src.models.enums import ExecutionStatus
from src.models.orm import AgentRun, Execution, SystemConfig, WorkflowRunDaily

pytestmark = pytest.mark.e2e


def _frozen_datetime(now: datetime) -> type[datetime]:
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.astimezone(tz)

    return FrozenDateTime


@pytest.mark.asyncio
async def test_report_usage_persists_one_installation_id_counts_the_completed_utc_day_and_acks_once(
    async_session_factory, monkeypatch
) -> None:
    now = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday = now.date() - timedelta(days=1)
    start = datetime.combine(yesterday, datetime.min.time(), tzinfo=UTC)
    end = start + timedelta(days=1)
    execution_ids: list = []
    agent_run_ids: list = []
    rollup_ids: list = []
    requests: list[httpx.Request] = []

    async with async_session_factory() as db:
        kept = [
            Execution(
                id=uuid4(),
                workflow_name="usage reporting",
                status=ExecutionStatus.SUCCESS,
                executed_by_name="test",
                started_at=start,
                completed_at=start + timedelta(seconds=1),
            ),
            Execution(
                id=uuid4(),
                workflow_name="usage reporting",
                status=ExecutionStatus.FAILED,
                executed_by_name="test",
                started_at=end - timedelta(microseconds=1),
                completed_at=end,
            ),
            Execution(
                id=uuid4(),
                workflow_name="usage reporting",
                status=ExecutionStatus.SUCCESS,
                executed_by_name="test",
                started_at=end,
                completed_at=end,
            ),
            Execution(
                id=uuid4(),
                workflow_name="usage reporting",
                status=ExecutionStatus.SCHEDULED,
                executed_by_name="test",
            ),
        ]
        rollups = [
            WorkflowRunDaily(
                id=uuid4(),
                day=yesterday,
                workflow_name="usage reporting",
                status=ExecutionStatus.SUCCESS,
                run_count=3,
                total_duration_ms=0,
                total_cpu_seconds=0,
            ),
            WorkflowRunDaily(
                id=uuid4(),
                day=now.date(),
                workflow_name="usage reporting",
                status=ExecutionStatus.SUCCESS,
                run_count=99,
                total_duration_ms=0,
                total_cpu_seconds=0,
            ),
        ]
        agent_runs = [
            AgentRun(
                id=uuid4(),
                trigger_type="test",
                status="completed",
                started_at=start,
                completed_at=end,
            ),
            AgentRun(
                id=uuid4(),
                trigger_type="test",
                status="completed",
                completed_at=end - timedelta(microseconds=1),
            ),
            AgentRun(
                id=uuid4(),
                trigger_type="test",
                status="completed",
                started_at=end,
                completed_at=end,
            ),
            AgentRun(id=uuid4(), trigger_type="test", status="queued"),
        ]
        execution_ids = [row.id for row in kept]
        agent_run_ids = [row.id for row in agent_runs]
        rollup_ids = [row.id for row in rollups]
        db.add_all([*kept, *rollups, *agent_runs])
        await db.commit()

    try:
        async with async_session_factory() as first_session:
            installation_id = await usage_reporting.get_or_create_installation_id(first_session)
        async with async_session_factory() as fresh_session:
            assert await usage_reporting.get_or_create_installation_id(fresh_session) == installation_id

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(204, request=request)

        transport = httpx.MockTransport(handler)
        real_async_client = httpx.AsyncClient
        monkeypatch.setattr(usage_reporting, "datetime", _frozen_datetime(now))
        monkeypatch.setattr(usage_reporting, "get_version", lambda: "v1.4.2")
        settings = SimpleNamespace(
            environment="production", debug=False, usage_reporting_enabled=True
        )
        monkeypatch.setattr(usage_reporting, "get_settings", lambda: settings)
        monkeypatch.setattr(
            usage_reporting.httpx,
            "AsyncClient",
            lambda **kwargs: real_async_client(transport=transport, **kwargs),
        )

        assert (await usage_reporting.report_usage()).summary == "Usage report delivered"
        assert (await usage_reporting.report_usage()).summary == "Usage report already delivered"

        assert len(requests) == 1
        assert requests[0].url == httpx.URL("https://reports.gobifrost.com/usage")
        assert json.loads(requests[0].content) == {
            "schema_version": 1,
            "installation_id": str(installation_id),
            "version": "1.4.2",
            "period": yesterday.isoformat(),
            "workflow_runs": 5,
            "agent_runs": 2,
            "environment": "production",
        }
    finally:
        async with async_session_factory() as cleanup:
            await cleanup.execute(
                delete(AgentRun).where(AgentRun.id.in_(agent_run_ids))
            )
            await cleanup.execute(
                delete(Execution).where(Execution.id.in_(execution_ids))
            )
            await cleanup.execute(
                delete(WorkflowRunDaily).where(WorkflowRunDaily.id.in_(rollup_ids))
            )
            await cleanup.execute(
                delete(SystemConfig).where(SystemConfig.category == "usage_reporting")
            )
            await cleanup.commit()
