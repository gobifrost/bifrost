"""
Run retention deleter against real Postgres.

Every call passes a 2002 cutoff and the seeded "old" rows finished in 2001, so
only the rows a test seeded are eligible, whatever else the shared stack holds.
"""

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.enums import AgentAccessLevel, EventSourceType, ExecutionStatus
from src.models.orm import (
    Agent,
    AgentRun,
    AgentRunStep,
    AIUsage,
    Event,
    EventDelivery,
    EventSource,
    EventSubscription,
    Execution,
    ExecutionLog,
    Organization,
    PlatformJob,
    Workflow,
    WorkflowRunDaily,
)
from src.services.platform_job_lease import LeaseLost
from src.services.run_retention.deleter import (
    delete_agent_run_batch,
    delete_event_batch,
    delete_workflow_run_batch,
    plan_run_retention,
)

CUTOFF = datetime(2002, 1, 1, tzinfo=UTC)
NOW = datetime.now(UTC)


@dataclass
class Seeded:
    org_id: UUID
    workflow_id: UUID
    wf_name: str
    agent_id: UUID
    source_id: UUID
    subscription_id: UUID
    old_ok: UUID
    old_failed: UUID
    old_running: UUID
    old_scheduled: UUID
    recent_ok: UUID
    a_parent: UUID
    a_child: UUID
    a_young_parent: UUID
    a_young_child: UUID
    a_paused: UUID
    old_event: UUID
    old_delivery: UUID
    recent_event: UUID
    execution_ids: list[UUID] = field(default_factory=list)

    @property
    def agent_run_ids(self) -> set[UUID]:
        return {self.a_parent, self.a_child, self.a_young_parent, self.a_young_child, self.a_paused}


def _execution(seeded_name: str, workflow_id: UUID, org_id: UUID, status: ExecutionStatus, *,
               started: datetime | None, completed: datetime | None, **metrics) -> Execution:
    return Execution(
        id=uuid4(),
        workflow_name=seeded_name,
        workflow_id=workflow_id,
        organization_id=org_id,
        status=status,
        executed_by_name="Retention Test",
        started_at=started,
        completed_at=completed,
        **metrics,
    )


def _agent_run(agent_id: UUID, org_id: UUID, status: str, completed: datetime | None,
               parent: UUID | None = None) -> AgentRun:
    return AgentRun(
        id=uuid4(),
        agent_id=agent_id,
        org_id=org_id,
        trigger_type="test",
        status=status,
        completed_at=completed,
        parent_run_id=parent,
    )


@pytest_asyncio.fixture
async def seeded(db_session: AsyncSession):
    hex_ = uuid4().hex[:12]
    org = Organization(id=uuid4(), name=f"Retention Org {hex_}", created_by="test")
    wf_name = f"rr-{hex_}"
    workflow = Workflow(
        id=uuid4(),
        name=wf_name,
        function_name="rr_test",
        path=f"workflows/rr_{hex_}.py",
        type="workflow",
        access_level="authenticated",
        is_active=True,
    )
    agent = Agent(
        id=uuid4(),
        name=f"rr-agent-{hex_}",
        description="run-retention",
        system_prompt="test",
        channels=["chat"],
        access_level=AgentAccessLevel.AUTHENTICATED,
        organization_id=None,
        is_active=True,
        knowledge_sources=[],
        system_tools=[],
        created_by="test@example.com",
        created_at=NOW,
        updated_at=NOW,
    )
    db_session.add_all([org, workflow, agent])
    await db_session.flush()

    day = datetime(2001, 1, 1, 10, 0, tzinfo=UTC)
    old_ok = _execution(
        wf_name, workflow.id, org.id, ExecutionStatus.SUCCESS,
        started=day, completed=datetime(2001, 1, 1, 10, 0, 5, tzinfo=UTC),
        duration_ms=5000, cpu_total_seconds=1.5, peak_cpu_cores=0.5,
        peak_process_rss_bytes=100, peak_memory_bytes=90,
    )
    old_failed = _execution(
        wf_name, workflow.id, org.id, ExecutionStatus.FAILED,
        started=datetime(2001, 1, 1, 11, 0, tzinfo=UTC), completed=datetime(2001, 1, 1, 11, 0, 1, tzinfo=UTC),
        duration_ms=1000, cpu_total_seconds=0.5, peak_cpu_cores=0.9,
    )
    old_running = _execution(wf_name, workflow.id, org.id, ExecutionStatus.RUNNING, started=day, completed=None)
    old_scheduled = _execution(wf_name, workflow.id, org.id, ExecutionStatus.SCHEDULED, started=None, completed=None)
    recent_ok = _execution(wf_name, workflow.id, org.id, ExecutionStatus.SUCCESS, started=NOW, completed=NOW)
    executions = [old_ok, old_failed, old_running, old_scheduled, recent_ok]

    old = datetime(2001, 1, 1, 12, 0, tzinfo=UTC)
    a_parent = _agent_run(agent.id, org.id, "completed", old)
    a_young_parent = _agent_run(agent.id, org.id, "completed", old)
    a_paused = _agent_run(agent.id, org.id, "paused", old)
    db_session.add_all([*executions, a_parent, a_young_parent, a_paused])
    await db_session.flush()
    a_child = _agent_run(agent.id, org.id, "completed", old, parent=a_parent.id)
    a_young_child = _agent_run(agent.id, org.id, "completed", NOW, parent=a_young_parent.id)
    db_session.add_all([a_child, a_young_child])
    await db_session.flush()

    source = EventSource(
        id=uuid4(),
        name=f"rr-source-{hex_}",
        source_type=EventSourceType.TOPIC,
        event_type=f"rr.{hex_}",
        organization_id=None,
        is_active=True,
        created_by="test@example.com",
    )
    subscription = EventSubscription(
        id=uuid4(),
        event_source_id=source.id,
        target_type="workflow",
        workflow_id=workflow.id,
        event_type=f"rr.{hex_}",
        is_active=True,
        created_by="test@example.com",
    )
    old_event = Event(id=uuid4(), event_source_id=source.id, data={}, created_at=old)
    recent_event = Event(id=uuid4(), event_source_id=source.id, data={})
    db_session.add_all([source, subscription])
    await db_session.flush()
    db_session.add_all([old_event, recent_event])
    await db_session.flush()
    old_delivery = EventDelivery(
        id=uuid4(), event_id=old_event.id, event_subscription_id=subscription.id, agent_run_id=a_parent.id
    )
    db_session.add_all([
        old_delivery,
        ExecutionLog(execution_id=old_ok.id, level="INFO", message="hello"),
        AgentRunStep(run_id=a_child.id, step_number=1, type="llm_request"),
        AIUsage(execution_id=old_ok.id, provider="openai", model="m", input_tokens=10, output_tokens=5,
                cost=Decimal("0.01")),
        AIUsage(agent_run_id=a_child.id, provider="openai", model="m", input_tokens=10, output_tokens=5,
                cost=Decimal("0.01")),
    ])
    await db_session.commit()

    data = Seeded(
        org_id=org.id, workflow_id=workflow.id, wf_name=wf_name, agent_id=agent.id,
        source_id=source.id, subscription_id=subscription.id,
        old_ok=old_ok.id, old_failed=old_failed.id, old_running=old_running.id,
        old_scheduled=old_scheduled.id, recent_ok=recent_ok.id,
        a_parent=a_parent.id, a_child=a_child.id, a_young_parent=a_young_parent.id,
        a_young_child=a_young_child.id, a_paused=a_paused.id,
        old_event=old_event.id, old_delivery=old_delivery.id, recent_event=recent_event.id,
        execution_ids=[e.id for e in executions],
    )
    # Tests delete these rows from other sessions; keep no stale objects here.
    db_session.expunge_all()

    yield data

    await db_session.rollback()
    await db_session.execute(delete(Event).where(Event.id.in_([data.old_event, data.recent_event])))
    await db_session.execute(delete(EventSubscription).where(EventSubscription.id == data.subscription_id))
    await db_session.execute(delete(EventSource).where(EventSource.id == data.source_id))
    await db_session.execute(delete(AIUsage).where(or_(
        AIUsage.execution_id.in_(data.execution_ids), AIUsage.agent_run_id.in_(data.agent_run_ids)
    )))
    await db_session.execute(delete(AgentRun).where(AgentRun.id.in_(data.agent_run_ids)))
    await db_session.execute(delete(Agent).where(Agent.id == data.agent_id))
    await db_session.execute(delete(Execution).where(Execution.id.in_(data.execution_ids)))
    await db_session.execute(delete(WorkflowRunDaily).where(WorkflowRunDaily.workflow_name == data.wf_name))
    await db_session.execute(delete(Workflow).where(Workflow.id == data.workflow_id))
    await db_session.execute(delete(Organization).where(Organization.id == data.org_id))
    await db_session.commit()


async def _executions(db: AsyncSession, seeded: Seeded) -> set[UUID]:
    return set((await db.execute(select(Execution.id).where(Execution.workflow_name == seeded.wf_name))).scalars())


async def _agent_runs(db: AsyncSession, seeded: Seeded) -> set[UUID]:
    return set((await db.execute(select(AgentRun.id).where(AgentRun.agent_id == seeded.agent_id))).scalars())


async def _events(db: AsyncSession, seeded: Seeded) -> set[UUID]:
    return set((await db.execute(select(Event.id).where(Event.event_source_id == seeded.source_id))).scalars())


async def _daily_rows(db: AsyncSession, seeded: Seeded) -> int:
    return (await db.execute(
        select(func.count()).select_from(WorkflowRunDaily).where(WorkflowRunDaily.workflow_name == seeded.wf_name)
    )).scalar_one()


async def _assert_all_present(db: AsyncSession, seeded: Seeded) -> None:
    assert await _executions(db, seeded) == set(seeded.execution_ids)
    assert await _agent_runs(db, seeded) == seeded.agent_run_ids
    assert await _events(db, seeded) == {seeded.old_event, seeded.recent_event}
    assert await db.scalar(select(func.count()).select_from(EventDelivery).where(
        EventDelivery.id == seeded.old_delivery)) == 1
    assert await db.scalar(select(func.count()).select_from(ExecutionLog).where(
        ExecutionLog.execution_id == seeded.old_ok)) == 1
    assert await db.scalar(select(func.count()).select_from(AgentRunStep).where(
        AgentRunStep.run_id == seeded.a_child)) == 1
    usage = (await db.execute(select(AIUsage.workflow_id, AIUsage.agent_id).where(or_(
        AIUsage.execution_id == seeded.old_ok, AIUsage.agent_run_id == seeded.a_child)))).all()
    assert usage == [(None, None), (None, None)]
    assert await _daily_rows(db, seeded) == 0


def _status(value: ExecutionStatus | str) -> str:
    return value.value if isinstance(value, ExecutionStatus) else value


async def test_workflow_batch_rolls_up_stamps_and_deletes(db_session, seeded, run_retention_lease):
    lease = run_retention_lease
    deleted = await delete_workflow_run_batch(cutoff=CUTOFF, job_id=lease.job_id, lease_token=lease.token)
    assert deleted == 2
    assert await _executions(db_session, seeded) == {seeded.old_running, seeded.old_scheduled, seeded.recent_ok}
    assert (await db_session.scalar(select(func.count()).select_from(ExecutionLog).where(
        ExecutionLog.execution_id == seeded.old_ok))) == 0
    usage = (await db_session.execute(select(AIUsage).where(AIUsage.execution_id == seeded.old_ok))).scalar_one()
    assert usage.workflow_id == seeded.workflow_id
    daily = {_status(r.status): r for r in (await db_session.execute(
        select(WorkflowRunDaily).where(WorkflowRunDaily.workflow_name == seeded.wf_name))).scalars()}
    assert set(daily) == {"Success", "Failed"}
    assert daily["Success"].day == date(2001, 1, 1)
    assert daily["Success"].organization_id == seeded.org_id
    assert daily["Success"].workflow_id == seeded.workflow_id
    assert (daily["Success"].run_count, daily["Success"].total_duration_ms, daily["Success"].total_cpu_seconds) == (
        1, 5000, 1.5)
    assert (daily["Success"].max_peak_cpu_cores, daily["Success"].max_peak_process_rss_bytes,
            daily["Success"].max_peak_memory_bytes) == (0.5, 100, 90)
    assert daily["Failed"].max_peak_cpu_cores == 0.9
    await db_session.commit()
    assert await delete_workflow_run_batch(cutoff=CUTOFF, job_id=lease.job_id, lease_token=lease.token) == 0


async def test_second_rollup_into_same_key_increments(db_session, seeded, run_retention_lease):
    lease = run_retention_lease
    third = _execution(
        seeded.wf_name, seeded.workflow_id, seeded.org_id, ExecutionStatus.SUCCESS,
        started=datetime(2001, 1, 1, 12, 0, tzinfo=UTC), completed=datetime(2001, 1, 1, 12, 0, 2, tzinfo=UTC),
        duration_ms=2000, cpu_total_seconds=0.25, peak_cpu_cores=0.7,
        peak_process_rss_bytes=300, peak_memory_bytes=50,
    )
    db_session.add(third)
    await db_session.commit()
    seeded.execution_ids.append(third.id)
    db_session.expunge_all()

    for _ in range(3):
        assert await delete_workflow_run_batch(
            cutoff=CUTOFF, job_id=lease.job_id, lease_token=lease.token, limit=1) == 1

    success = (await db_session.execute(select(WorkflowRunDaily).where(
        WorkflowRunDaily.workflow_name == seeded.wf_name,
        WorkflowRunDaily.status == ExecutionStatus.SUCCESS,
    ))).scalar_one()
    assert (success.run_count, success.total_duration_ms, success.total_cpu_seconds) == (2, 7000, 1.75)
    assert (success.max_peak_cpu_cores, success.max_peak_process_rss_bytes, success.max_peak_memory_bytes) == (
        0.7, 300, 90)
    assert await _daily_rows(db_session, seeded) == 2


async def test_agent_batch_deletes_leaves_first_and_never_cascades(db_session, seeded, run_retention_lease):
    lease = run_retention_lease
    first = await delete_agent_run_batch(cutoff=CUTOFF, job_id=lease.job_id, lease_token=lease.token)
    # leaves only: a_child (a_parent has a child; a_young_parent has a child; a_paused is paused)
    assert first == 1
    assert await _agent_runs(db_session, seeded) == seeded.agent_run_ids - {seeded.a_child}
    await db_session.commit()
    second = await delete_agent_run_batch(cutoff=CUTOFF, job_id=lease.job_id, lease_token=lease.token)
    assert second == 1  # a_parent is now a leaf
    assert await delete_agent_run_batch(cutoff=CUTOFF, job_id=lease.job_id, lease_token=lease.token) == 0
    kept = await _agent_runs(db_session, seeded)
    assert kept == {seeded.a_young_parent, seeded.a_young_child, seeded.a_paused}
    usage = (await db_session.execute(select(AIUsage).where(AIUsage.agent_run_id == seeded.a_child))).scalar_one()
    assert usage.agent_id == seeded.agent_id
    assert (await db_session.scalar(select(func.count()).select_from(AgentRunStep).where(
        AgentRunStep.run_id == seeded.a_child))) == 0
    # The old event outlives the run it delivered to; only the link is cleared.
    delivery = await db_session.scalar(select(EventDelivery.agent_run_id).where(
        EventDelivery.id == seeded.old_delivery))
    assert delivery is None


async def test_event_batch_deletes_old_events_and_deliveries(db_session, seeded, run_retention_lease):
    lease = run_retention_lease
    assert await delete_event_batch(cutoff=CUTOFF, job_id=lease.job_id, lease_token=lease.token) == 1
    assert await _events(db_session, seeded) == {seeded.recent_event}
    assert await db_session.scalar(select(func.count()).select_from(EventDelivery).where(
        EventDelivery.id == seeded.old_delivery)) == 0
    assert seeded.a_parent in await _agent_runs(db_session, seeded)
    await db_session.commit()
    assert await delete_event_batch(cutoff=CUTOFF, job_id=lease.job_id, lease_token=lease.token) == 0


async def test_stale_lease_deletes_nothing(db_session, seeded, run_retention_lease):
    lease = run_retention_lease
    await db_session.execute(update(PlatformJob).where(PlatformJob.id == lease.job_id).values(lease_token=uuid4()))
    await db_session.commit()
    for fn in (delete_workflow_run_batch, delete_agent_run_batch, delete_event_batch):
        with pytest.raises(LeaseLost):
            await fn(cutoff=CUTOFF, job_id=lease.job_id, lease_token=lease.token)
    await _assert_all_present(db_session, seeded)


async def test_plan_counts_without_writing(db_session, seeded):
    plan = await plan_run_retention(db_session, cutoff=CUTOFF)
    await db_session.commit()
    assert plan["cutoff"] == CUTOFF.isoformat()
    assert plan["workflow_runs"] >= 2 and plan["agent_runs"] >= 3 and plan["events"] >= 1
    # plan counts every finished old agent run (a_parent, a_child, a_young_parent), paused excluded
    assert plan["oldest_workflow_run"] <= datetime(2001, 1, 1, 10, 0, 5, tzinfo=UTC).isoformat()
    assert plan["oldest_agent_run"] <= datetime(2001, 1, 1, 12, 0, tzinfo=UTC).isoformat()
    assert plan["oldest_event"] <= datetime(2001, 1, 1, 12, 0, tzinfo=UTC).isoformat()
    await _assert_all_present(db_session, seeded)
