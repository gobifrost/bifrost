"""Grouping audit events: counts and the newest sample per group."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories.audit_logs import AuditLogRepository


@pytest_asyncio.fixture
async def db_session(async_engine):
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            try:
                yield session
            finally:
                await session.rollback()
                await transaction.rollback()


async def _check(
    repo: AuditLogRepository,
    execution_id,
    *,
    workflow: str | None,
    outcome: str,
    created_at: datetime | None = None,
    resource_type: str | None = "scope_switch",
    operation_id: str | None = None,
    trace_user_id: str | None = None,
):
    row = await repo.create(
        action="access.check",
        user_id=None,
        organization_id=None,
        resource_type=resource_type,
        resource_id=None,
        outcome=outcome,
        source="http",
        ip_address=None,
        user_agent=None,
        details={
            "enforced": False,
            "workflow_id": workflow,
            **({"trace": {"steps": [{"facts": {"user_id": trace_user_id}}]}} if trace_user_id else {}),
        },
        execution_id=execution_id,
        operation_id=operation_id,
    )
    if created_at is not None:
        row.created_at = created_at
        await repo.session.flush()
    return row


async def test_groups_count_by_workflow_with_the_newest_sample(db_session: AsyncSession) -> None:
    repo = AuditLogRepository(db_session)
    run = uuid4()
    first, second = str(uuid4()), str(uuid4())
    await _check(repo, run, workflow=first, outcome="failure")
    newest = await _check(repo, run, workflow=first, outcome="failure")
    await _check(repo, run, workflow=second, outcome="success")

    groups = await repo.group("workflow", action_prefix="access.check", execution_id=run)

    assert [(group.key, group.count) for group in groups] == [(first, 2), (second, 1)]
    assert groups[0].sample.id == newest.id


async def test_groups_respect_the_filters(db_session: AsyncSession) -> None:
    repo = AuditLogRepository(db_session)
    run = uuid4()
    await _check(repo, run, workflow=None, outcome="failure")
    await _check(repo, run, workflow=None, outcome="success")

    groups = await repo.group("outcome", action_prefix="access.check", execution_id=run, outcome="failure")

    assert [(group.key, group.count) for group in groups] == [("failure", 1)]


async def test_access_check_aggregation_uses_utc_buckets_and_excludes_check_gaps(db_session: AsyncSession) -> None:
    repo = AuditLogRepository(db_session)
    run = uuid4()
    workflow = str(uuid4())
    earlier = await _check(
        repo,
        run,
        workflow=workflow,
        outcome="failure",
        created_at=datetime(2026, 10, 5, 10, 59, tzinfo=UTC),
        operation_id="workflows.run",
    )
    newest = await _check(
        repo,
        run,
        workflow=workflow,
        outcome="failure",
        created_at=datetime(2026, 10, 5, 11, 1, tzinfo=UTC),
        operation_id="workflows.run",
    )
    await repo.create(
        action="access.check_gap",
        user_id=None,
        organization_id=None,
        resource_type="scope_switch",
        resource_id=None,
        outcome="failure",
        source="http",
        ip_address=None,
        user_agent=None,
        details={"workflow_id": workflow},
        execution_id=run,
        operation_id="workflows.run",
    )

    groups, token = await repo.aggregate("1h", action_exact="access.check", limit=10)

    assert token is None
    assert [(group.count, group.window_start, group.window_end, group.sample.id) for group in groups] == [
        (1, datetime(2026, 10, 5, 11, tzinfo=UTC), datetime(2026, 10, 5, 12, tzinfo=UTC), newest.id),
        (1, datetime(2026, 10, 5, 10, tzinfo=UTC), datetime(2026, 10, 5, 11, tzinfo=UTC), earlier.id),
    ]
    assert groups[0].first_seen == groups[0].last_seen == newest.created_at


async def test_access_check_aggregation_paginates_tied_samples_with_nullable_dimensions(
    db_session: AsyncSession,
) -> None:
    repo = AuditLogRepository(db_session)
    at = datetime(2026, 10, 5, 11, 15, tzinfo=UTC)
    await _check(repo, uuid4(), workflow=None, outcome="failure", created_at=at, resource_type=None)
    await _check(repo, uuid4(), workflow=None, outcome="failure", created_at=at, resource_type="scope_switch")

    first_page, token = await repo.aggregate("1h", action_exact="access.check", limit=1)
    second_page, final_token = await repo.aggregate(
        "1h", action_exact="access.check", limit=1, continuation_token=token
    )

    assert token is not None
    assert final_token is None
    assert {first_page[0].resource_type, second_page[0].resource_type} == {None, "scope_switch"}


async def test_access_check_aggregation_keeps_each_composite_dimension_separate(
    db_session: AsyncSession,
) -> None:
    repo = AuditLogRepository(db_session)
    at = datetime(2026, 10, 5, 11, 15, tzinfo=UTC)
    workflow = str(uuid4())
    other_workflow = str(uuid4())
    await _check(repo, uuid4(), workflow=workflow, outcome="failure", created_at=at, operation_id="workflows.run")
    await _check(repo, uuid4(), workflow=workflow, outcome="failure", created_at=at, operation_id="workflows.run")
    await _check(
        repo, uuid4(), workflow=other_workflow, outcome="failure", created_at=at, operation_id="workflows.run"
    )
    await _check(repo, uuid4(), workflow=workflow, outcome="success", created_at=at, operation_id="workflows.run")
    await _check(repo, uuid4(), workflow=workflow, outcome="failure", created_at=at, operation_id="workflows.read")

    groups, _ = await repo.aggregate("1h", action_exact="access.check", limit=10)

    assert sorted(
        (group.count, group.selector.workflow_id, group.selector.operation_id, group.selector.outcome)
        for group in groups
    ) == sorted(
        [
            (2, workflow, "workflows.run", "failure"),
            (1, workflow, "workflows.read", "failure"),
            (1, workflow, "workflows.run", "success"),
            (1, other_workflow, "workflows.run", "failure"),
        ]
    )


async def test_access_check_aggregation_keeps_deleted_run_users_separate(db_session: AsyncSession) -> None:
    repo = AuditLogRepository(db_session)
    at = datetime(2026, 10, 5, 11, 15, tzinfo=UTC)
    first_user, second_user = str(uuid4()), str(uuid4())
    await _check(repo, uuid4(), workflow=None, outcome="failure", created_at=at, trace_user_id=first_user)
    await _check(repo, uuid4(), workflow=None, outcome="failure", created_at=at, trace_user_id=second_user)

    groups, _ = await repo.aggregate("1h", action_exact="access.check", limit=10)
    first = next(group for group in groups if str(group.selector.user_id) == first_user)
    second = next(group for group in groups if str(group.selector.user_id) == second_user)
    rows, _ = await repo.list(action_exact="access.check", aggregate_selector=first.selector)

    assert (first.count, second.count) == (1, 1)
    assert [row.id for row in rows] == [first.sample.id]
