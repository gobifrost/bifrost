"""Grouping audit events: counts and the newest sample per group."""

from __future__ import annotations

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


async def _check(repo: AuditLogRepository, execution_id, *, workflow: str | None, outcome: str):
    return await repo.create(
        action="access.check",
        user_id=None,
        organization_id=None,
        resource_type="scope_switch",
        resource_id=None,
        outcome=outcome,
        source="http",
        ip_address=None,
        user_agent=None,
        details={"enforced": False, "workflow_id": workflow},
        execution_id=execution_id,
    )


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
