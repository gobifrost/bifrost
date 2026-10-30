"""Epoch reads, the one-statement validation query and the protocol record."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.models.enums import ExecutionStatus
from src.models.orm.agent_runs import AgentRun
from src.models.orm.executions import Execution
from src.models.orm.organizations import Organization
from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.models.orm.users import User
from src.models.orm.workflows import Workflow
from src.services.authorization.authz import (
    AuthorityStateMissing,
    read_authority_state,
    read_epoch,
    record_protocol_start,
)


async def _person(session: AsyncSession) -> tuple[Organization, User]:
    organization = Organization(name=f"Authz State {uuid4().hex[:8]}", created_by="authz-state")
    session.add(organization)
    await session.flush()
    user = User(email=f"authz-state-{uuid4().hex[:8]}@example.test", name="State", organization_id=organization.id)
    session.add(user)
    await session.flush()
    return organization, user


async def _execution(session: AsyncSession, status: ExecutionStatus) -> Execution:
    organization, user = await _person(session)
    execution = Execution(
        workflow_name="state", executed_by_name="State", run_user_id=user.id, executed_by=user.id,
        organization_id=organization.id, status=status, started_by_user_id=user.id, root_execution_id=uuid4(),
    )
    session.add(execution)
    await session.flush()
    return execution


async def _attempt(session: AsyncSession, *, state: str, lease: timedelta) -> ServiceAttempt:
    organization, user = await _person(session)
    name = f"state_{uuid4().hex[:8]}"
    workflow = Workflow(name=name, function_name=name, path=f"workflows/{name}.py", organization_id=organization.id)
    session.add(workflow)
    await session.flush()
    definition = ServiceDefinition(workflow_id=workflow.id, organization_id=organization.id, created_by="authz-state")
    session.add(definition)
    await session.flush()
    attempt = ServiceAttempt(
        service_id=definition.id, run_user_id=user.id, lease_token="state-lease",
        lease_expires_at=datetime.now(timezone.utc) + lease, state=state,
    )
    session.add(attempt)
    await session.flush()
    return attempt


async def test_the_validation_query_is_one_statement_returning_the_epoch(db_session: AsyncSession) -> None:
    execution = await _execution(db_session, ExecutionStatus.RUNNING)
    epoch = await read_epoch(db_session)
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        state = await read_authority_state(
            db_session, "workflow_execution", execution.id, service_id=None, now=datetime.now(timezone.utc)
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(statements) == 1
    assert state.epoch == epoch
    assert state.live is True


@pytest.mark.parametrize(
    ("status", "live"),
    [
        (ExecutionStatus.PENDING, True),
        (ExecutionStatus.RUNNING, True),
        (ExecutionStatus.CANCELLING, True),
        (ExecutionStatus.SUCCESS, False),
        (ExecutionStatus.FAILED, False),
    ],
)
async def test_workflow_execution_liveness(db_session: AsyncSession, status: ExecutionStatus, live: bool) -> None:
    execution = await _execution(db_session, status)

    state = await read_authority_state(
        db_session, "workflow_execution", execution.id, service_id=None, now=datetime.now(timezone.utc)
    )

    assert state.live is live


@pytest.mark.parametrize(("status", "live"), [("running", True), ("cancelling", True), ("completed", False)])
async def test_agent_run_liveness(db_session: AsyncSession, status: str, live: bool) -> None:
    organization, user = await _person(db_session)
    run = AgentRun(trigger_type="chat", status=status, org_id=organization.id, run_user_id=user.id, acting_user_id=user.id)
    db_session.add(run)
    await db_session.flush()

    state = await read_authority_state(db_session, "agent_run", run.id, service_id=None, now=datetime.now(timezone.utc))

    assert state.live is live


async def test_service_attempt_liveness_needs_state_lease_and_service(db_session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    live = await _attempt(db_session, state="running", lease=timedelta(minutes=1))
    expired = await _attempt(db_session, state="running", lease=timedelta(seconds=-1))
    stopped = await _attempt(db_session, state="stopped", lease=timedelta(minutes=1))

    async def is_live(attempt: ServiceAttempt, service_id: UUID | None) -> bool:
        return (
            await read_authority_state(db_session, "service_attempt", attempt.id, service_id=service_id, now=now)
        ).live

    assert await is_live(live, live.service_id) is True
    assert await is_live(live, uuid4()) is False
    assert await is_live(live, None) is False
    assert await is_live(expired, expired.service_id) is False
    assert await is_live(stopped, stopped.service_id) is False


async def test_a_missing_run_is_not_live(db_session: AsyncSession) -> None:
    state = await read_authority_state(
        db_session, "workflow_execution", uuid4(), service_id=None, now=datetime.now(timezone.utc)
    )

    assert state.epoch == await read_epoch(db_session)
    assert state.live is False


async def test_a_missing_epoch_row_is_reported_never_defaulted(async_engine: AsyncEngine) -> None:
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text("ALTER TABLE authz_versions DISABLE TRIGGER authz_versions_no_delete"))
            await connection.execute(text("DELETE FROM authz_versions"))
            session = AsyncSession(bind=connection)

            state = await read_authority_state(
                session, "workflow_execution", uuid4(), service_id=None, now=datetime.now(timezone.utc)
            )
            assert state.epoch is None
            assert state.live is False
            with pytest.raises(AuthorityStateMissing):
                await read_epoch(session)
        finally:
            await transaction.rollback()


async def test_the_protocol_start_record_keeps_the_first_start(async_engine: AsyncEngine) -> None:
    query = text("SELECT first_started_at FROM authz_protocol_state WHERE protocol = 1")
    async with async_engine.begin() as connection:
        first = (await connection.execute(query)).scalar_one_or_none()
    if first is None:
        await record_protocol_start()
        async with async_engine.connect() as connection:
            first = (await connection.execute(query)).scalar_one()

    await record_protocol_start()

    async with async_engine.connect() as connection:
        assert (await connection.execute(query)).scalar_one() == first
