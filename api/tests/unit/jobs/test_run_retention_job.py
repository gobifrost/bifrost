"""Business semantics of the run.retention platform-job handler.

Database and deleter calls are replaced with fakes that keep the real
signatures; the deleter itself is covered against Postgres in
tests/e2e/platform/test_run_retention_deleter.py.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, AsyncGenerator
from uuid import UUID, uuid4

import pytest

from src.jobs.platform import run_retention
from src.jobs.platform.base import PlatformJobCancelled, PlatformJobFailure
from src.jobs.platform.run_retention import (
    RUN_RETENTION_DEFINITION,
    RunRetentionPayload,
    run_run_retention,
)
from src.jobs.platform.registry import get_platform_job_definition
from src.models.contracts.run_retention import RunRetentionSettings
from src.scheduler.registry import SCHEDULED_TASKS_BY_ID
from src.services.platform_job_lease import LeaseLost
from src.services.run_retention.deleter import DeleteMismatch

FROZEN_CUTOFF = datetime(2020, 3, 1, tzinfo=UTC)
TABLES = ("workflow", "agent", "events")


class FakeContext:
    def __init__(self, checkpoint: dict[str, Any] | None = None) -> None:
        self.job_id = uuid4()
        self.lease_token = uuid4()
        self.checkpoint = checkpoint
        self.checkpoints: list[tuple[dict[str, Any], str]] = []
        self.reports: list[tuple[str, int]] = []
        self.events: list[str] = []

    async def report(
        self,
        phase: str,
        current: int = 0,
        total: int | None = None,
        percent: float | None = None,
    ) -> None:
        self.reports.append((phase, current))
        self.events.append("report")

    async def log(self, level: str, code: str, message: str) -> None:
        return None

    async def save_checkpoint(self, result: dict[str, Any], *, phase: str) -> None:
        self.checkpoints.append((dict(result), phase))


class FakeSettingsService:
    days: int | None = 30

    def __init__(self, session: object) -> None:
        self.session = session

    async def get_settings(self) -> RunRetentionSettings:
        return RunRetentionSettings(days=self.days)


class Deleter:
    """Batch fakes: each table returns its queued counts, then 0."""

    def __init__(self, counts: dict[str, list[int]], context: FakeContext | None = None) -> None:
        self.counts = counts
        self.context = context
        self.calls: list[tuple[str, datetime, UUID, UUID]] = []
        self.planned: list[datetime] = []

    def _next(self, table: str, cutoff: datetime, job_id: UUID, lease_token: UUID) -> int:
        self.calls.append((table, cutoff, job_id, lease_token))
        if self.context is not None:
            self.context.events.append(f"batch:{table}")
        queued = self.counts.get(table, [])
        return queued.pop(0) if queued else 0

    async def delete_workflow_run_batch(
        self, *, cutoff: datetime, job_id: UUID, lease_token: UUID, limit: int = 1000
    ) -> int:
        return self._next("workflow", cutoff, job_id, lease_token)

    async def delete_agent_run_batch(
        self, *, cutoff: datetime, job_id: UUID, lease_token: UUID, limit: int = 500
    ) -> int:
        return self._next("agent", cutoff, job_id, lease_token)

    async def delete_event_batch(
        self, *, cutoff: datetime, job_id: UUID, lease_token: UUID, limit: int = 1000
    ) -> int:
        return self._next("events", cutoff, job_id, lease_token)

    async def plan_run_retention(self, db: object, *, cutoff: datetime) -> dict[str, Any]:
        self.planned.append(cutoff)
        return {
            "cutoff": cutoff.isoformat(),
            "workflow_runs": 4,
            "agent_runs": 2,
            "events": 9,
            "oldest_workflow_run": None,
            "oldest_agent_run": None,
            "oldest_event": None,
        }


def _install(monkeypatch: pytest.MonkeyPatch, deleter: Deleter) -> None:
    @asynccontextmanager
    async def fake_db_context() -> AsyncGenerator[object, None]:
        yield object()

    monkeypatch.setattr(run_retention, "get_db_context", fake_db_context)
    monkeypatch.setattr(run_retention, "RunRetentionSettingsService", FakeSettingsService)
    monkeypatch.setattr(run_retention, "delete_workflow_run_batch", deleter.delete_workflow_run_batch)
    monkeypatch.setattr(run_retention, "delete_agent_run_batch", deleter.delete_agent_run_batch)
    monkeypatch.setattr(run_retention, "delete_event_batch", deleter.delete_event_batch)
    monkeypatch.setattr(run_retention, "plan_run_retention", deleter.plan_run_retention)


def _checkpoint(**counts: int) -> dict[str, Any]:
    return {
        "dry_run": False,
        "cutoff": FROZEN_CUTOFF.isoformat(),
        "workflow_runs_deleted": counts.get("workflow", 0),
        "agent_runs_deleted": counts.get("agent", 0),
        "events_deleted": counts.get("events", 0),
        "continues": False,
    }


def test_definition_policy() -> None:
    policy = RUN_RETENTION_DEFINITION.policy

    assert RUN_RETENTION_DEFINITION.job_type == "run.retention"
    assert RUN_RETENTION_DEFINITION.payload_model is RunRetentionPayload
    assert policy.timeout_seconds == 2 * 60 * 60
    assert policy.max_attempts == 3
    assert policy.max_concurrency == 1
    assert policy.retry_on_runner_loss is True
    assert policy.retry_on_failure is False
    assert policy.min_memory_headroom_mb == 256
    assert policy.allow_running_cancellation is True


def test_registered_and_scheduled_in_place_of_event_cleanup() -> None:
    assert get_platform_job_definition("run.retention") is RUN_RETENTION_DEFINITION
    scheduled = SCHEDULED_TASKS_BY_ID["run_retention"]
    assert (scheduled.name, scheduled.schedule, scheduled.execution_mode) == (
        "Run Retention",
        "Daily at 03:00 UTC",
        "durable_job",
    )
    assert "event_cleanup" not in SCHEDULED_TASKS_BY_ID


@pytest.mark.asyncio
async def test_keep_forever_deletes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    deleter = Deleter({"workflow": [5]})
    _install(monkeypatch, deleter)
    monkeypatch.setattr(FakeSettingsService, "days", None)

    result = await run_run_retention(FakeContext(), RunRetentionPayload())

    assert result == {"dry_run": False, "skipped": "keep_forever"}
    assert deleter.calls == []


@pytest.mark.asyncio
async def test_resume_reuses_frozen_cutoff(monkeypatch: pytest.MonkeyPatch) -> None:
    class SettingsMustNotBeRead(FakeSettingsService):
        async def get_settings(self) -> RunRetentionSettings:
            raise AssertionError("a resumed run must keep its frozen cutoff")

    deleter = Deleter({"workflow": [3], "agent": [2], "events": [1]})
    _install(monkeypatch, deleter)
    monkeypatch.setattr(run_retention, "RunRetentionSettingsService", SettingsMustNotBeRead)
    context = FakeContext(checkpoint=_checkpoint(workflow=10))

    result = await run_run_retention(context, RunRetentionPayload())

    assert deleter.calls and {cutoff for _, cutoff, _, _ in deleter.calls} == {FROZEN_CUTOFF}
    assert {(job, lease) for _, _, job, lease in deleter.calls} == {(context.job_id, context.lease_token)}
    assert result["cutoff"] == FROZEN_CUTOFF.isoformat()
    assert result["workflow_runs_deleted"] == 13


@pytest.mark.asyncio
async def test_loops_each_table_until_empty_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    deleter = Deleter({"workflow": [1000, 7], "agent": [500, 500, 3], "events": [42]})
    _install(monkeypatch, deleter)
    before = datetime.now(UTC)

    result = await run_run_retention(FakeContext(), RunRetentionPayload())

    assert [table for table, *_ in deleter.calls] == [
        "workflow", "workflow", "workflow",
        "agent", "agent", "agent", "agent",
        "events", "events",
    ]
    assert result["workflow_runs_deleted"] == 1007
    assert result["agent_runs_deleted"] == 1003
    assert result["events_deleted"] == 42
    assert result["continues"] is False
    assert result["dry_run"] is False
    cutoff = datetime.fromisoformat(result["cutoff"])
    assert before.timestamp() - 30 * 86400 - 1 <= cutoff.timestamp() <= datetime.now(UTC).timestamp() - 30 * 86400


@pytest.mark.asyncio
async def test_cap_stops_each_table_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    deleter = Deleter({table: [1] * 10 for table in TABLES})
    _install(monkeypatch, deleter)
    monkeypatch.setattr(run_retention, "MAX_ROWS_PER_TABLE", 2)

    result = await run_run_retention(FakeContext(), RunRetentionPayload())

    assert [table for table, *_ in deleter.calls] == [
        "workflow", "workflow", "agent", "agent", "events", "events",
    ]
    assert (result["workflow_runs_deleted"], result["agent_runs_deleted"], result["events_deleted"]) == (2, 2, 2)
    assert result["continues"] is True


@pytest.mark.asyncio
async def test_lease_lost_cancels(monkeypatch: pytest.MonkeyPatch) -> None:
    deleter = Deleter({})
    _install(monkeypatch, deleter)

    async def lease_lost(
        *, cutoff: datetime, job_id: UUID, lease_token: UUID, limit: int = 500
    ) -> int:
        raise LeaseLost(str(job_id))

    monkeypatch.setattr(run_retention, "delete_agent_run_batch", lease_lost)

    with pytest.raises(PlatformJobCancelled):
        await run_run_retention(FakeContext(), RunRetentionPayload())


@pytest.mark.asyncio
async def test_delete_mismatch_fails_with_partial_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    deleter = Deleter({"workflow": [4]})
    _install(monkeypatch, deleter)

    async def mismatch(
        *, cutoff: datetime, job_id: UUID, lease_token: UUID, limit: int = 500
    ) -> int:
        raise DeleteMismatch("agent runs: deleted 1 of 2")

    monkeypatch.setattr(run_retention, "delete_agent_run_batch", mismatch)

    with pytest.raises(PlatformJobFailure) as failure:
        await run_run_retention(FakeContext(), RunRetentionPayload())

    assert failure.value.code == "run_delete_mismatch"
    assert "rolled back" in failure.value.message
    assert failure.value.retryable is True
    result = failure.value.result
    assert result is not None
    assert (result["workflow_runs_deleted"], result["agent_runs_deleted"]) == (4, 0)


@pytest.mark.asyncio
async def test_dry_run_plans_without_deleting(monkeypatch: pytest.MonkeyPatch) -> None:
    deleter = Deleter({"workflow": [5]})
    _install(monkeypatch, deleter)
    context = FakeContext()

    result = await run_run_retention(context, RunRetentionPayload(dry_run=True))

    assert deleter.calls == []
    assert context.checkpoints == []
    assert len(deleter.planned) == 1
    assert result["dry_run"] is True
    assert result["workflow_runs"] == 4
    assert result["events"] == 9
    assert result["cutoff"] == deleter.planned[0].isoformat()


@pytest.mark.asyncio
async def test_reports_after_every_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    context = FakeContext()
    deleter = Deleter({"workflow": [2, 3], "agent": [1], "events": [6]}, context=context)
    _install(monkeypatch, deleter)

    await run_run_retention(context, RunRetentionPayload())

    assert context.events == [
        "batch:workflow", "report",
        "batch:workflow", "report",
        "batch:workflow",
        "batch:agent", "report",
        "batch:agent",
        "batch:events", "report",
        "batch:events",
    ]
    assert context.reports == [
        ("Deleted 2 workflow runs", 2),
        ("Deleted 5 workflow runs", 5),
        ("Deleted 1 agent runs", 1),
        ("Deleted 6 events", 6),
    ]
