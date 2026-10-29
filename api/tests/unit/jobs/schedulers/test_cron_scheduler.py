"""Unit tests for cron_scheduler overlap policy enforcement.

TDD: these tests were written before the implementation.

Note on DB isolation: each test commits data to its own DB session.
process_schedule_sources queries ALL active SCHEDULE sources, so leaked
rows from prior tests may be visible. Assertions use per-source Event
counts rather than the global results["events_created"] counter, except
for skipped_overlap which is source-specific enough to be trustworthy.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.models.enums import EventDeliveryStatus, EventSourceType, EventStatus, ScheduleOverlapPolicy
from src.models.orm.events import Event, EventDelivery, EventSource, EventSubscription, ScheduleSource
from src.models.orm.scheduler_diagnostics import SchedulerTaskState

PATH_DB_CTX = "src.jobs.schedulers.cron_scheduler.get_db_context"
# EventSubscriptionRepository is imported at module level in cron_scheduler
PATH_SUB_REPO = "src.jobs.schedulers.cron_scheduler.EventSubscriptionRepository"
# EventProcessor is imported inside the function body; patch the source module
PATH_PROCESSOR = "src.services.events.processor.EventProcessor"
# is_cron_expression_valid is imported inside the function body; patch the source module
PATH_IS_VALID = "src.services.cron_parser.is_cron_expression_valid"


class _DbCtx:
    """Async context manager that yields the test's real DB session."""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_args):
        return False


class _CommitTrackingSession:
    """Delegate to a real session while recording transaction commits."""

    def __init__(self, session):
        self._session = session
        self.commit_count = 0

    def __getattr__(self, name):
        return getattr(self._session, name)

    async def commit(self):
        self.commit_count += 1
        await self._session.commit()


def _queued(count: int, on_hand_off=None):  # type: ignore[no-untyped-def]
    """A queued batch of ``count`` runs, as queue_event_deliveries returns."""
    from src.services.events.processor import QueuedDeliveries

    async def hand_off() -> None:
        if on_hand_off is not None:
            on_hand_off()

    return QueuedDeliveries(hand_offs=[hand_off for _ in range(count)])


@pytest.fixture(autouse=True)
async def _no_schedule_left_running(db_session):
    """Tests commit every-minute schedules; disable them afterwards so a later
    test that crosses a minute boundary does not see them fire again."""
    yield
    from sqlalchemy import update

    await db_session.rollback()
    await db_session.execute(update(ScheduleSource).values(enabled=False))
    await db_session.commit()


def _make_source_and_subscription(
    *,
    cron: str = "* * * * *",
    overlap_policy: ScheduleOverlapPolicy = ScheduleOverlapPolicy.SKIP,
    target_type: str = "workflow",
) -> tuple[EventSource, ScheduleSource, EventSubscription]:
    source_id = uuid4()
    source = EventSource(
        id=source_id,
        name=f"test-schedule-{source_id.hex[:6]}",
        source_type=EventSourceType.SCHEDULE,
        is_active=True,
        created_by="test",
    )
    ss = ScheduleSource(
        id=uuid4(),
        event_source_id=source_id,
        cron_expression=cron,
        timezone="UTC",
        enabled=True,
        overlap_policy=overlap_policy,
    )
    sub = EventSubscription(
        id=uuid4(),
        event_source_id=source_id,
        workflow_id=None,
        target_type=target_type,
        is_active=True,
        created_by="test",
    )
    return source, ss, sub


def _make_event(source_id) -> Event:
    return Event(
        id=uuid4(),
        event_source_id=source_id,
        event_type="schedule.fired",
        received_at=datetime.now(timezone.utc),
        data={"cron_expression": "* * * * *"},
        status=EventStatus.COMPLETED,
    )


def _make_delivery(
    *,
    event_id,
    subscription_id,
    status: EventDeliveryStatus = EventDeliveryStatus.SUCCESS,
    execution_id=None,
    agent_run_id=None,
) -> EventDelivery:
    return EventDelivery(
        id=uuid4(),
        event_id=event_id,
        event_subscription_id=subscription_id,
        workflow_id=None,
        execution_id=execution_id,
        agent_run_id=agent_run_id,
        status=status,
    )


async def _count_events_for_source(db_session, source_id) -> int:
    rows = (
        await db_session.execute(select(Event).where(Event.event_source_id == source_id))
    ).scalars().all()
    return len(rows)


async def _events_for_source(db_session, source_id) -> list[Event]:
    rows = (
        await db_session.execute(select(Event).where(Event.event_source_id == source_id))
    ).scalars().all()
    return list(rows)


@pytest.mark.asyncio
async def test_schedule_evaluates_cron_in_configured_timezone(db_session):
    """A 9 AM New York schedule should fire at 14:00 UTC during EST."""
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = cls(2026, 1, 1, 14, 0, 30, tzinfo=timezone.utc)
            if tz is None:
                return value.replace(tzinfo=None)
            return value.astimezone(tz)

    source, ss, sub = _make_source_and_subscription(cron="0 9 * * *")
    ss.timezone = "America/New_York"
    db_session.add(source)
    db_session.add(ss)
    db_session.add(sub)
    await db_session.commit()

    mock_sub_repo = AsyncMock()
    mock_sub_repo.get_active_for_event = AsyncMock(return_value=[])
    mock_processor = AsyncMock()
    mock_processor.queue_event_deliveries = AsyncMock(return_value=0)

    from src.jobs.schedulers.cron_scheduler import process_schedule_sources

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_IS_VALID, return_value=True),
        patch(PATH_SUB_REPO, return_value=mock_sub_repo),
        patch(PATH_PROCESSOR, return_value=mock_processor),
        patch("src.jobs.schedulers.cron_scheduler.datetime", FrozenDateTime),
    ):
        await process_schedule_sources()

    events = await _events_for_source(db_session, source.id)
    assert len(events) == 1
    assert events[0].received_at.isoformat() == "2026-01-01T14:00:00+00:00"
    assert events[0].data["scheduled_time"] == "2026-01-01T14:00:00+00:00"
    assert events[0].data["timezone"] == "America/New_York"


def test_scheduler_resolves_legacy_indianapolis_alias():
    from src.jobs.schedulers.cron_scheduler import _latest_due_run_utc

    now = datetime(2026, 7, 22, 13, 0, 30, tzinfo=timezone.utc)
    due = _latest_due_run_utc(
        "0 9 * * *",
        now,
        "America/Indianapolis",
        now - timedelta(seconds=60),
    )

    assert due == datetime(2026, 7, 22, 13, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_schedule_fires_when_no_active_executions(db_session):
    """Happy path: no prior executions → event is created for this source."""
    source, ss, sub = _make_source_and_subscription(overlap_policy=ScheduleOverlapPolicy.SKIP)
    db_session.add(source)
    db_session.add(ss)
    db_session.add(sub)
    await db_session.commit()

    before = await _count_events_for_source(db_session, source.id)
    assert before == 0, "Setup sanity: no prior events"

    mock_sub_repo = AsyncMock()
    mock_sub_repo.get_active_for_event = AsyncMock(return_value=[])
    mock_processor = AsyncMock()
    mock_processor.queue_event_deliveries = AsyncMock(return_value=0)

    from src.jobs.schedulers.cron_scheduler import process_schedule_sources

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_IS_VALID, return_value=True),
        patch(PATH_SUB_REPO, return_value=mock_sub_repo),
        patch(PATH_PROCESSOR, return_value=mock_processor),
    ):
        await process_schedule_sources()

    after = await _count_events_for_source(db_session, source.id)
    assert after == 1, "Expected one event to be created for this source"


@pytest.mark.asyncio
async def test_schedule_skipped_when_previous_run_active_skip_policy(db_session, caplog):
    """overlap_policy=SKIP with an active (RUNNING) prior Execution → no new Event."""
    source, ss, sub = _make_source_and_subscription(overlap_policy=ScheduleOverlapPolicy.SKIP)
    db_session.add(source)
    db_session.add(ss)
    db_session.add(sub)
    await db_session.flush()

    # Prior event from a previous fire
    prior_event = _make_event(source.id)
    db_session.add(prior_event)
    await db_session.flush()

    # An active delivery (still PENDING — downstream Execution not yet materialized or in-flight)
    delivery = _make_delivery(
        event_id=prior_event.id,
        subscription_id=sub.id,
        status=EventDeliveryStatus.PENDING,
    )
    db_session.add(delivery)
    await db_session.commit()

    before = await _count_events_for_source(db_session, source.id)
    assert before == 1, "Setup sanity: 1 prior event"

    mock_sub_repo = AsyncMock()
    mock_sub_repo.get_active_for_event = AsyncMock(return_value=[])
    mock_processor = AsyncMock()

    from src.jobs.schedulers.cron_scheduler import process_schedule_sources
    import logging

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_IS_VALID, return_value=True),
        patch(PATH_SUB_REPO, return_value=mock_sub_repo),
        patch(PATH_PROCESSOR, return_value=mock_processor),
        caplog.at_level(logging.INFO, logger="src.jobs.schedulers.cron_scheduler"),
    ):
        results = await process_schedule_sources()

    assert results.get("skipped_overlap", 0) >= 1, "Expected skipped_overlap counter >= 1"
    assert any("schedule_skipped_overlap" in r.message for r in caplog.records), (
        "Expected a schedule_skipped_overlap log entry"
    )

    # Confirm no new Event row was persisted for this source
    after = await _count_events_for_source(db_session, source.id)
    assert after == 1, "Expected exactly 1 event (the prior one), not a new one"


@pytest.mark.asyncio
async def test_schedule_skipped_when_previous_run_active_queue_policy_v1_fallback(db_session, caplog):
    """overlap_policy=QUEUE → behaves as SKIP in v1, emits a WARNING about fallback."""
    source, ss, sub = _make_source_and_subscription(overlap_policy=ScheduleOverlapPolicy.QUEUE)
    db_session.add(source)
    db_session.add(ss)
    db_session.add(sub)
    await db_session.flush()

    prior_event = _make_event(source.id)
    db_session.add(prior_event)
    await db_session.flush()

    delivery = _make_delivery(
        event_id=prior_event.id,
        subscription_id=sub.id,
        status=EventDeliveryStatus.QUEUED,
    )
    db_session.add(delivery)
    await db_session.commit()

    before = await _count_events_for_source(db_session, source.id)
    assert before == 1, "Setup sanity: 1 prior event"

    mock_sub_repo = AsyncMock()
    mock_sub_repo.get_active_for_event = AsyncMock(return_value=[])
    mock_processor = AsyncMock()

    from src.jobs.schedulers.cron_scheduler import process_schedule_sources
    import logging

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_IS_VALID, return_value=True),
        patch(PATH_SUB_REPO, return_value=mock_sub_repo),
        patch(PATH_PROCESSOR, return_value=mock_processor),
        caplog.at_level(logging.WARNING, logger="src.jobs.schedulers.cron_scheduler"),
    ):
        results = await process_schedule_sources()

    # No new event for this specific source
    after = await _count_events_for_source(db_session, source.id)
    assert after == 1, "QUEUE policy should behave as SKIP in v1 — no new event"
    assert results.get("skipped_overlap", 0) >= 1
    assert any(
        "schedule_overlap_policy_not_implemented" in r.message for r in caplog.records
    ), "Expected a v1-fallback WARNING for non-SKIP policy"


@pytest.mark.asyncio
async def test_schedule_fires_when_only_completed_executions(db_session):
    """Prior executions with terminal status (SUCCESS, FAILED) must not block firing."""
    source, ss, sub = _make_source_and_subscription(overlap_policy=ScheduleOverlapPolicy.SKIP)
    db_session.add(source)
    db_session.add(ss)
    db_session.add(sub)
    await db_session.flush()

    prior_event = _make_event(source.id)
    db_session.add(prior_event)
    await db_session.flush()

    # Two historical terminal deliveries (SUCCESS and FAILED — both non-blocking)
    for terminal_status in (EventDeliveryStatus.SUCCESS, EventDeliveryStatus.FAILED):
        delivery = _make_delivery(
            event_id=prior_event.id,
            subscription_id=sub.id,
            status=terminal_status,
        )
        db_session.add(delivery)

    await db_session.commit()

    before = await _count_events_for_source(db_session, source.id)
    assert before == 1, "Setup sanity: 1 prior event with terminal executions"

    mock_sub_repo = AsyncMock()
    mock_sub_repo.get_active_for_event = AsyncMock(return_value=[])
    mock_processor = AsyncMock()
    mock_processor.queue_event_deliveries = AsyncMock(return_value=0)

    from src.jobs.schedulers.cron_scheduler import process_schedule_sources

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_IS_VALID, return_value=True),
        patch(PATH_SUB_REPO, return_value=mock_sub_repo),
        patch(PATH_PROCESSOR, return_value=mock_processor),
    ):
        await process_schedule_sources()

    after = await _count_events_for_source(db_session, source.id)
    assert after == 2, "Expected a new event fired (terminal executions must not block)"
    # We cannot assert skipped_overlap == 0 globally because prior committed test data
    # (from other tests using the same DB) may have active executions on other sources.


@pytest.mark.asyncio
async def test_schedule_skipped_when_active_agent_run_target(db_session, caplog):
    """Schedule with overlap_policy=SKIP and an active EventDelivery targeting an
    AGENT subscription (agent_run_id set, execution_id=None) must skip the new fire.

    This tests the gap that the previous Execution-join query missed: agent-target
    deliveries set agent_run_id and leave execution_id=None, so the old join on
    EventDelivery.execution_id == Execution.id never matched them.
    """
    source, ss, sub = _make_source_and_subscription(
        overlap_policy=ScheduleOverlapPolicy.SKIP,
        target_type="agent",
    )
    db_session.add(source)
    db_session.add(ss)
    db_session.add(sub)
    await db_session.flush()

    # Prior event from a previous fire
    prior_event = _make_event(source.id)
    db_session.add(prior_event)
    await db_session.flush()

    # Active delivery to an agent subscription — execution_id is None, agent_run_id would
    # normally be set but may not yet be (delivery is PENDING pre-materialization).
    delivery = _make_delivery(
        event_id=prior_event.id,
        subscription_id=sub.id,
        status=EventDeliveryStatus.PENDING,
        execution_id=None,
        agent_run_id=None,
    )
    db_session.add(delivery)
    await db_session.commit()

    before = await _count_events_for_source(db_session, source.id)
    assert before == 1, "Setup sanity: 1 prior event"

    mock_sub_repo = AsyncMock()
    mock_sub_repo.get_active_for_event = AsyncMock(return_value=[])
    mock_processor = AsyncMock()

    from src.jobs.schedulers.cron_scheduler import process_schedule_sources
    import logging

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_IS_VALID, return_value=True),
        patch(PATH_SUB_REPO, return_value=mock_sub_repo),
        patch(PATH_PROCESSOR, return_value=mock_processor),
        caplog.at_level(logging.INFO, logger="src.jobs.schedulers.cron_scheduler"),
    ):
        results = await process_schedule_sources()

    assert results.get("skipped_overlap", 0) >= 1, "Expected skipped_overlap counter >= 1"
    assert any("schedule_skipped_overlap" in r.message for r in caplog.records), (
        "Expected a schedule_skipped_overlap log entry"
    )

    # No new Event row must have been created for this source
    after = await _count_events_for_source(db_session, source.id)
    assert after == 1, "Expected exactly 1 event (the prior one) — agent-target overlap must block"


async def _deliveries_for_source(db_session, source_id) -> list[EventDelivery]:
    rows = (
        await db_session.execute(
            select(EventDelivery)
            .join(Event, Event.id == EventDelivery.event_id)
            .where(Event.event_source_id == source_id)
        )
    ).scalars().all()
    return list(rows)


@pytest.mark.asyncio
async def test_schedule_creates_delivery_for_agent_subscription(db_session):
    """Regression: a fired schedule must create a delivery for an AGENT-target
    subscription.

    Agent subscriptions carry agent_id and leave workflow_id NULL by design. The
    scheduler previously skipped any subscription without a workflow_id, so no
    EventDelivery was ever created and the agent never ran (the UI mislabeled it
    "Subscription added after this event arrived"). Workflows were unaffected.
    """
    from src.models.orm.agents import Agent

    agent = Agent(
        id=uuid4(),
        name="sched-agent",
        system_prompt="you are a test agent",
        created_by="test",
    )
    source, ss, sub = _make_source_and_subscription(target_type="agent")
    sub.agent_id = agent.id  # agent target: agent_id set, workflow_id stays None
    db_session.add(agent)
    db_session.add(source)
    db_session.add(ss)
    db_session.add(sub)
    await db_session.commit()

    mock_sub_repo = AsyncMock()
    mock_sub_repo.get_active_for_event = AsyncMock(return_value=[sub])
    mock_processor = AsyncMock()
    mock_processor.queue_event_deliveries = AsyncMock(return_value=_queued(1))

    from src.jobs.schedulers.cron_scheduler import process_schedule_sources

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_IS_VALID, return_value=True),
        patch(PATH_SUB_REPO, return_value=mock_sub_repo),
        patch(PATH_PROCESSOR, return_value=mock_processor),
    ):
        await process_schedule_sources()

    deliveries = await _deliveries_for_source(db_session, source.id)
    assert len(deliveries) == 1, "agent subscription should produce exactly one delivery"
    assert deliveries[0].event_subscription_id == sub.id
    assert deliveries[0].workflow_id is None  # agent target carries no workflow_id


@pytest.mark.asyncio
async def test_schedule_commits_runs_with_their_deliveries_then_hands_them_off(db_session):
    """A source's event, deliveries and runs commit together, then go to workers."""
    from src.models.orm.agents import Agent

    agent = Agent(
        id=uuid4(),
        name="sched-agent-transaction-order",
        system_prompt="you are a test agent",
        created_by="test",
    )
    source, ss, sub = _make_source_and_subscription(target_type="agent")
    sub.agent_id = agent.id
    db_session.add_all([agent, source, ss, sub])
    await db_session.commit()

    tracking_session = _CommitTrackingSession(db_session)
    mock_sub_repo = AsyncMock()
    mock_sub_repo.get_active_for_event = AsyncMock(return_value=[sub])
    mock_processor = AsyncMock()
    commits_at: dict[str, int] = {}

    async def queue(_event_id):  # type: ignore[no-untyped-def]
        commits_at["queue"] = tracking_session.commit_count
        return _queued(
            1, on_hand_off=lambda: commits_at.setdefault("hand_off", tracking_session.commit_count)
        )

    mock_processor.queue_event_deliveries = AsyncMock(side_effect=queue)

    from src.jobs.schedulers.cron_scheduler import process_schedule_sources

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(tracking_session)),
        patch(PATH_IS_VALID, return_value=True),
        patch(PATH_SUB_REPO, return_value=mock_sub_repo),
        patch(PATH_PROCESSOR, return_value=mock_processor),
    ):
        results = await process_schedule_sources()

    mock_processor.queue_event_deliveries.assert_awaited_once()
    assert results["errors"] == []
    assert results["deliveries_queued"] == 1
    # Queued before the source's commit; handed off only after it.
    assert commits_at["hand_off"] == commits_at["queue"] + 1


@pytest.mark.asyncio
async def test_schedule_creates_delivery_for_workflow_subscription(db_session):
    """Workflow-target subscriptions still get a delivery (no regression)."""
    from src.models.orm.workflows import Workflow

    workflow = Workflow(
        id=uuid4(),
        name="sched-wf",
        function_name="sched_wf",
        path="workflows/sched_wf.py",
    )
    source, ss, sub = _make_source_and_subscription(target_type="workflow")
    sub.workflow_id = workflow.id
    db_session.add(workflow)
    db_session.add(source)
    db_session.add(ss)
    db_session.add(sub)
    await db_session.commit()

    mock_sub_repo = AsyncMock()
    mock_sub_repo.get_active_for_event = AsyncMock(return_value=[sub])
    mock_processor = AsyncMock()
    mock_processor.queue_event_deliveries = AsyncMock(return_value=_queued(1))

    from src.jobs.schedulers.cron_scheduler import process_schedule_sources

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_IS_VALID, return_value=True),
        patch(PATH_SUB_REPO, return_value=mock_sub_repo),
        patch(PATH_PROCESSOR, return_value=mock_processor),
    ):
        await process_schedule_sources()

    deliveries = await _deliveries_for_source(db_session, source.id)
    assert len(deliveries) == 1
    assert deliveries[0].workflow_id == sub.workflow_id


# --- Occurrence identity across trigger-leader failover ----------------------


def _frozen_at(moment: datetime) -> type[datetime]:
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            value = cls.fromtimestamp(moment.timestamp(), tz=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    return Frozen


async def _scan(db_session, moment: datetime) -> dict:
    from src.jobs.schedulers.cron_scheduler import process_schedule_sources

    mock_sub_repo = AsyncMock()
    mock_sub_repo.get_active_for_event = AsyncMock(return_value=[])
    mock_processor = AsyncMock()
    mock_processor.queue_event_deliveries = AsyncMock(return_value=0)
    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_IS_VALID, return_value=True),
        patch(PATH_SUB_REPO, return_value=mock_sub_repo),
        patch(PATH_PROCESSOR, return_value=mock_processor),
        patch("src.jobs.schedulers.cron_scheduler.datetime", _frozen_at(moment)),
    ):
        return await process_schedule_sources()


@pytest.fixture
async def processor_cursor(db_session):
    """The schedule_processor task state a trigger leader publishes."""
    from sqlalchemy import delete

    await db_session.execute(
        delete(SchedulerTaskState).where(SchedulerTaskState.task_id == "schedule_processor")
    )
    state = SchedulerTaskState(
        task_id="schedule_processor",
        name="Process Schedule Sources",
        schedule="Every minute",
        execution_mode="leader",
    )
    db_session.add(state)
    await db_session.commit()
    yield state
    await db_session.execute(
        delete(SchedulerTaskState).where(SchedulerTaskState.task_id == "schedule_processor")
    )
    await db_session.commit()


async def _add_source(db_session, cron: str, tz: str = "UTC"):
    source, ss, sub = _make_source_and_subscription(cron=cron)
    ss.timezone = tz
    db_session.add_all([source, ss, sub])
    await db_session.commit()
    return source


@pytest.mark.asyncio
async def test_overlapping_leaders_fire_one_occurrence_once(db_session, processor_cursor):
    source = await _add_source(db_session, "0 * * * *")
    moment = datetime(2026, 3, 2, 10, 0, 20, tzinfo=timezone.utc)

    first = await _scan(db_session, moment)
    second = await _scan(db_session, moment + timedelta(seconds=5))

    events = await _events_for_source(db_session, source.id)
    assert len(events) == 1
    assert events[0].occurrence_key == (
        f"schedule:{source.id}:2026-03-02T10:00:00+00:00"
    )
    assert first.get("duplicates_skipped", 0) == 0
    assert second["duplicates_skipped"] >= 1


@pytest.mark.asyncio
async def test_successor_leader_fires_occurrence_missed_during_failover(
    db_session, processor_cursor
):
    source = await _add_source(db_session, "0 * * * *")
    # The previous leader last scanned at 09:59:50 and then stopped; the
    # successor's first scan is at 10:02:30, after the 10:00 occurrence.
    processor_cursor.cursor_at = datetime(2026, 3, 2, 9, 59, 50, tzinfo=timezone.utc)
    await db_session.commit()

    moment = datetime(2026, 3, 2, 10, 2, 30, tzinfo=timezone.utc)
    await _scan(db_session, moment)

    events = await _events_for_source(db_session, source.id)
    assert [event.received_at for event in events] == [
        datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)
    ]
    await db_session.refresh(processor_cursor)
    assert processor_cursor.cursor_at == moment

    # The next scan neither repeats nor rewinds the occurrence.
    await _scan(db_session, moment + timedelta(minutes=1))
    assert len(await _events_for_source(db_session, source.id)) == 1


@pytest.mark.asyncio
async def test_without_a_recorded_scan_the_window_is_one_cadence(
    db_session, processor_cursor
):
    source = await _add_source(db_session, "0 * * * *")

    await _scan(db_session, datetime(2026, 3, 2, 10, 2, 30, tzinfo=timezone.utc))

    assert await _events_for_source(db_session, source.id) == []


@pytest.mark.asyncio
async def test_catch_up_is_bounded(db_session, processor_cursor):
    source = await _add_source(db_session, "30 * * * *")
    processor_cursor.cursor_at = datetime(2026, 3, 2, 8, 0, tzinfo=timezone.utc)
    await db_session.commit()

    # 09:30 is outside MAX_CATCH_UP of a 10:00:30 scan; it does not replay.
    await _scan(db_session, datetime(2026, 3, 2, 10, 0, 30, tzinfo=timezone.utc))

    assert await _events_for_source(db_session, source.id) == []


@pytest.mark.asyncio
async def test_dst_repeated_wall_clock_hour_fires_distinct_instants(
    db_session, processor_cursor
):
    """01:30 occurs twice in New York on 2026-11-01; each instant is keyed in UTC."""
    source = await _add_source(db_session, "30 * * * *", "America/New_York")

    for moment in (
        datetime(2026, 11, 1, 5, 30, 20, tzinfo=timezone.utc),  # 01:30 EDT
        datetime(2026, 11, 1, 5, 30, 40, tzinfo=timezone.utc),  # same instant, rescanned
        datetime(2026, 11, 1, 6, 30, 20, tzinfo=timezone.utc),  # 01:30 EST
    ):
        await _scan(db_session, moment)

    events = await _events_for_source(db_session, source.id)
    assert sorted(event.received_at for event in events) == [
        datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc),
        datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc),
    ]
    assert len({event.occurrence_key for event in events}) == 2


@pytest.mark.asyncio
async def test_occurrence_fired_by_an_older_leader_is_not_repeated(
    db_session, processor_cursor
):
    """Mixed versions: an older image fires without an occurrence key."""
    source = await _add_source(db_session, "5 * * * *")
    processor_cursor.cursor_at = datetime(2026, 3, 2, 12, 1, tzinfo=timezone.utc)
    legacy = _make_event(source.id)
    legacy.received_at = datetime(2026, 3, 2, 12, 5, tzinfo=timezone.utc)
    db_session.add(legacy)
    await db_session.commit()

    result = await _scan(db_session, datetime(2026, 3, 2, 12, 8, 10, tzinfo=timezone.utc))

    events = await _events_for_source(db_session, source.id)
    assert [event.id for event in events] == [legacy.id]
    assert result["duplicates_skipped"] >= 1
