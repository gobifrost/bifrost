"""Tests for cancelling unstarted runs when their run user is deleted."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.models.enums import ExecutionStatus, IdentityKind
from src.models.orm.executions import Execution
from src.models.orm.organizations import Organization
from src.models.orm.users import User


@pytest.mark.asyncio
async def test_cancels_every_unstarted_run_for_the_deleted_user(db_session):
    from shared.run_cancellation import (
        RUN_USER_DELETED_ERROR,
        cancel_unstarted_runs_for_deleted_user,
    )

    organization = Organization(
        name=f"Contoso {uuid4().hex[:8]}", created_by="run-cancellation-test"
    )
    db_session.add(organization)
    await db_session.flush()
    user = User(
        email=f"departed-{uuid4().hex}@contoso.example",
        name="Departed",
        organization_id=organization.id,
    )
    db_session.add(user)
    await db_session.flush()

    scheduled = Execution(
        workflow_name="scheduled",
        executed_by=user.id,
        executed_by_name="Departed",
        run_user_id=user.id,
        status=ExecutionStatus.SCHEDULED,
        scheduled_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    delayed = Execution(
        workflow_name="delayed",
        executed_by=user.id,
        executed_by_name="Departed",
        run_user_id=user.id,
        status=ExecutionStatus.SCHEDULED,
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    queued = Execution(
        workflow_name="queued",
        executed_by=user.id,
        executed_by_name="Departed",
        run_user_id=user.id,
        status=ExecutionStatus.PENDING,
    )
    running = Execution(
        workflow_name="running",
        executed_by=user.id,
        executed_by_name="Departed",
        run_user_id=user.id,
        status=ExecutionStatus.RUNNING,
    )
    db_session.add_all([scheduled, delayed, queued, running])
    await db_session.flush()

    cancelled = await cancel_unstarted_runs_for_deleted_user(db_session, user.id)

    assert cancelled == 3
    for execution in (scheduled, delayed, queued):
        await db_session.refresh(execution)
        assert execution.status is ExecutionStatus.CANCELLED
        assert execution.error_message == RUN_USER_DELETED_ERROR
        assert execution.completed_at is not None
    await db_session.refresh(running)
    assert running.status is ExecutionStatus.RUNNING


@pytest.mark.asyncio
async def test_cancelled_queued_run_cannot_be_claimed_for_dispatch(db_session):
    from shared.run_cancellation import claim_pending_run_for_dispatch

    execution = Execution(
        workflow_name="already-cancelled",
        executed_by_name="Departed",
        status=ExecutionStatus.CANCELLED,
    )
    db_session.add(execution)
    await db_session.flush()

    assert await claim_pending_run_for_dispatch(db_session, execution.id) is False
    await db_session.refresh(execution)
    assert execution.status is ExecutionStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancels_unstarted_run_for_a_deleted_custom_identity(db_session):
    from shared.run_cancellation import cancel_unstarted_runs_for_deleted_user

    organization = Organization(
        name=f"Contoso {uuid4().hex[:8]}", created_by="run-cancellation-test"
    )
    db_session.add(organization)
    await db_session.flush()
    identity = User(
        email=f"identity-{uuid4().hex}@contoso.example",
        name="Contoso Automation",
        organization_id=organization.id,
        identity_kind=IdentityKind.CUSTOM,
    )
    db_session.add(identity)
    await db_session.flush()
    execution = Execution(
        workflow_name="identity-run",
        executed_by=identity.id,
        executed_by_name="Contoso Automation",
        run_user_id=identity.id,
        status=ExecutionStatus.SCHEDULED,
    )
    db_session.add(execution)
    await db_session.flush()

    assert await cancel_unstarted_runs_for_deleted_user(db_session, identity.id) == 1

    await db_session.refresh(execution)
    assert execution.status is ExecutionStatus.CANCELLED
