"""Unit tests for the deferred execution promoter."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio

from src.models.enums import ExecutionStatus
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.executions import Execution


PATH_PUBLISH = "src.jobs.schedulers.deferred_execution_promoter.hand_off_workflow_run"
PATH_DB_CTX = "src.jobs.schedulers.deferred_execution_promoter.get_db_context"


@pytest_asyncio.fixture
async def db_session(async_engine, monkeypatch):
    # The promoter commits through this same session. Keep those commits inside
    # a savepoint so scheduled rows and run users never leak into another test.
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
        ) as session:
            # The writer must see the same uncommitted test users. Its real
            # insert/judging logic runs here; REST coverage tests the detached
            # transaction boundary against committed users.
            monkeypatch.setattr(
                "src.services.access_check_writer.get_db_context", lambda: _DbCtx(session)
            )
            try:
                yield session
            finally:
                await session.rollback()
                await transaction.rollback()


@pytest_asyncio.fixture(autouse=True)
async def _fresh_redis_client(monkeypatch):
    # A refused Run As is audited through the shared Redis client, which binds
    # to the event loop that created it; each test runs in its own loop.
    from src.core.cache import redis_client
    from src.core.cache.redis_client import close_shared_redis

    monkeypatch.setattr(redis_client, "_shared_client", None)
    yield
    await close_shared_redis()


def _new_scheduled(when: datetime) -> Execution:
    # workflow_id/executed_by left None to avoid FK constraints in unit tests.
    return Execution(
        id=uuid4(),
        workflow_id=None,
        workflow_name="demo",
        status=ExecutionStatus.SCHEDULED,
        parameters={"k": 1},
        scheduled_at=when,
        executed_by=None,
        executed_by_name="user",
    )


class _DbCtx:
    """Async context manager that yields the test's session.

    Ensures the promoter runs against the same engine/event loop as the
    test fixture, avoiding cross-loop task errors from the global engine.
    """

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *_args):
        return False


@pytest.mark.asyncio
async def test_promotes_due_rows(db_session):
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    due = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    db_session.add(due)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock(return_value=True)) as pub,
    ):
        promoted = await promote_due_executions()

    assert promoted == 1
    pub.assert_awaited_once()

    await db_session.refresh(due)
    assert due.status == ExecutionStatus.PENDING


@pytest.mark.asyncio
async def test_deletion_cancellation_wins_after_promotion_before_dispatch(db_session):
    """A queued message cannot resurrect a run cancelled after promotion."""
    from shared.run_cancellation import (
        RUN_USER_DELETED_ERROR,
        cancel_unstarted_runs_for_deleted_user,
        claim_pending_run_for_dispatch,
    )
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    _org, user = await _contoso_person(db_session)
    due = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    due.executed_by = user.id
    due.run_user_id = user.id
    db_session.add(due)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock(return_value=True)),
    ):
        promoted = await promote_due_executions()

    assert promoted == 1
    assert await cancel_unstarted_runs_for_deleted_user(db_session, user.id) == 1
    await db_session.commit()

    assert await claim_pending_run_for_dispatch(db_session, due.id) is False
    await db_session.refresh(due)
    assert (due.status, due.error_message) == (
        ExecutionStatus.CANCELLED,
        RUN_USER_DELETED_ERROR,
    )


@pytest.mark.asyncio
async def test_does_not_promote_row_with_deleted_run_user_snapshot(db_session):
    from shared.run_cancellation import RUN_USER_DELETED_ERROR
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    due = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    due.execution_context = {"run_user_id": str(uuid4())}
    db_session.add(due)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock(return_value=True)) as publish,
    ):
        promoted = await promote_due_executions()

    assert promoted == 0
    publish.assert_not_awaited()
    await db_session.refresh(due)
    assert (due.status, due.error_message) == (
        ExecutionStatus.CANCELLED,
        RUN_USER_DELETED_ERROR,
    )


@pytest.mark.asyncio
async def test_leaves_future_rows(db_session):
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    future = _new_scheduled(datetime.now(timezone.utc) + timedelta(hours=1))
    db_session.add(future)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock(return_value=True)) as pub,
    ):
        promoted = await promote_due_executions()

    assert promoted == 0
    pub.assert_not_awaited()

    await db_session.refresh(future)
    assert future.status == ExecutionStatus.SCHEDULED


@pytest.mark.asyncio
async def test_skips_cancelled_rows(db_session):
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    cancelled = _new_scheduled(datetime.now(timezone.utc) - timedelta(minutes=1))
    cancelled.status = ExecutionStatus.CANCELLED
    db_session.add(cancelled)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock(return_value=True)),
    ):
        promoted = await promote_due_executions()

    assert promoted == 0
    await db_session.refresh(cancelled)
    assert cancelled.status == ExecutionStatus.CANCELLED


@pytest.mark.asyncio
async def test_promotion_commits_a_claimable_dispatch_before_the_hand_off(db_session):
    """The hand-off only wakes executors: the dispatch is already durable."""
    from sqlalchemy import select

    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions
    from src.models.orm.work_dispatches import WorkDispatch

    due = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    db_session.add(due)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock()),
    ):
        promoted = await promote_due_executions()

    assert promoted == 1
    await db_session.refresh(due)
    assert due.status == ExecutionStatus.PENDING
    dispatch = (
        await db_session.execute(select(WorkDispatch).where(WorkDispatch.subject_id == due.id))
    ).scalar_one()
    assert dispatch.assigned_worker_id is None
    assert dispatch.payload["execution_id"] == str(due.id)


@pytest.mark.asyncio
async def test_forwards_the_rows_persisted_actor_attribution(db_session):
    from sqlalchemy import select

    from src.models.enums import IdentityKind
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions
    from src.models.orm.users import User

    identity = (
        await db_session.execute(select(User.id).where(User.identity_kind == IdentityKind.GLOBAL_DEFAULT))
    ).scalar_one()
    due = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    due.run_user_id = identity
    due.started_by_user_id = identity
    due.root_execution_id = due.id
    due.impersonated_by_user_id = uuid4()
    due.impersonation_session_id = uuid4()
    plain = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    db_session.add_all([due, plain])
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock(return_value=True)) as pub,
    ):
        await promote_due_executions()

    pending = {call.args[0].pending["execution_id"]: call.args[0].pending for call in pub.await_args_list}
    lineage = {execution_id: record["lineage"] for execution_id, record in pending.items()}
    assert lineage[str(due.id)] == {
        "run_user_id": str(identity),
        "started_by_user_id": str(identity),
        "root_execution_id": str(due.id),
    }
    assert lineage[str(plain.id)] is None
    provenance = {
        execution_id: record["impersonation_provenance"]
        for execution_id, record in pending.items()
    }
    assert provenance[str(due.id)] == {
        "impersonated_by_user_id": str(due.impersonated_by_user_id),
        "impersonation_session_id": str(due.impersonation_session_id),
    }
    assert provenance[str(plain.id)] is None


async def _contoso_person(db_session, **fields):
    from src.models.orm.organizations import Organization
    from src.models.orm.users import User

    org = Organization(name=f"Contoso {uuid4().hex[:8]}", created_by="promoter-test")
    db_session.add(org)
    await db_session.flush()
    user = User(
        email=f"{uuid4().hex[:8]}@contoso.example", name="Contoso Person", organization_id=org.id, **fields
    )
    db_session.add(user)
    await db_session.flush()
    return org, user


async def _impersonation(db_session):
    """A Contoso initiator holding Impersonate Users at Contoso, and a colleague."""
    from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary

    org, initiator = await _contoso_person(db_session)
    colleague = User(email=f"{uuid4().hex[:8]}@contoso.example", name="Contoso Colleague", organization_id=org.id)
    role = Role(name=f"Contoso Impersonation {uuid4().hex[:8]}", created_by="promoter-test")
    db_session.add_all([colleague, role])
    await db_session.flush()
    db_session.add_all(
        [
            RolePermission(role_id=role.id, permission="users.impersonate"),
            UserRole(user_id=initiator.id, role_id=role.id, assigned_by="promoter-test"),
        ]
    )
    await db_session.flush()
    db_session.add(
        UserRoleBoundary(user_id=initiator.id, role_id=role.id, kind="organization", organization_id=org.id)
    )
    await db_session.flush()
    return initiator, colleague, role


def _acting_as(target_id, authorized_by, *, enforced: bool, run_user_id=None) -> Execution:
    """A due row acting as ``target_id``: a person's (``enforced``), or a
    workflow's decided for the run of ``run_user_id``."""
    row = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    row.executed_by = target_id
    run_as = {"user_id": str(target_id), "authorized_by": str(authorized_by), "enforced": enforced}
    if not enforced:
        run_as.update(run_user_id=str(run_user_id), workflow_id=None, execution_id=str(uuid4()))
    row.execution_context = {"is_platform_admin": False, "run_as": run_as}
    return row


async def _promote(db_session) -> AsyncMock:
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock(return_value=True)) as pub,
    ):
        await promote_due_executions()
    return pub


def _published(pub: AsyncMock) -> set[str]:
    return {call.args[0].pending["execution_id"] for call in pub.await_args_list}


async def _run_as_audit(db_session, user_id):
    from sqlalchemy import select

    from src.models.orm.audit import AuditLog

    query = select(AuditLog).where(AuditLog.user_id == user_id, AuditLog.resource_type == "run_as")
    return list((await db_session.execute(query)).scalars().all())


@pytest.mark.asyncio
async def test_a_run_as_whose_grant_was_revoked_fails_and_is_not_published(db_session):
    from sqlalchemy import delete

    from src.models.orm.users import UserRole, UserRoleBoundary

    initiator, colleague, role = await _impersonation(db_session)
    row = _acting_as(colleague.id, initiator.id, enforced=True)
    db_session.add(row)
    await db_session.commit()
    await db_session.execute(delete(UserRoleBoundary).where(UserRoleBoundary.role_id == role.id))
    await db_session.execute(delete(UserRole).where(UserRole.role_id == role.id))
    await db_session.commit()

    pub = await _promote(db_session)

    published = _published(pub)
    row_id = str(row.id)
    assert row_id not in published
    await db_session.refresh(row)
    assert row.status == ExecutionStatus.FAILED
    assert row.error_message == "Run As is no longer permitted: You don't have permission to run as this user"
    assert row.completed_at is not None
    # Recorded as a live refusal is: the initiator, the user and their organization, enforced.
    [audit] = await _run_as_audit(db_session, initiator.id)
    assert (audit.action, audit.outcome, audit.organization_id) == (
        "access.check",
        "failure",
        colleague.organization_id,
    )
    assert audit.details is not None
    colleague_id = str(colleague.id)
    assert (audit.details["enforced"], audit.details["inputs"]["run_as_user_id"]) == (True, colleague_id)
    stopped = [step["key"] for step in audit.details["trace"]["steps"] if step["status"] == "stopped"]
    assert stopped == ["permission"]


@pytest.mark.asyncio
async def test_a_run_as_whose_target_was_deactivated_fails(db_session):
    initiator, colleague, _role = await _impersonation(db_session)
    row = _acting_as(colleague.id, initiator.id, enforced=True)
    colleague.is_active = False
    db_session.add(row)
    await db_session.commit()

    pub = await _promote(db_session)

    published = _published(pub)
    row_id = str(row.id)
    assert row_id not in published
    await db_session.refresh(row)
    assert row.status == ExecutionStatus.FAILED
    expected = f"Run As is no longer permitted: Run As user '{colleague.id}' is inactive"
    assert row.error_message == expected
    [audit] = await _run_as_audit(db_session, initiator.id)
    assert audit.details is not None
    stopped = [(step["key"], step["reason"]) for step in audit.details["trace"]["steps"] if step["status"] == "stopped"]
    assert (audit.outcome, audit.details["enforced"], stopped) == ("failure", True, [("run_as_user", "inactive")])


@pytest.mark.asyncio
async def test_a_run_as_whose_initiator_is_inactive_fails(db_session):
    initiator, colleague, _role = await _impersonation(db_session)
    row = _acting_as(colleague.id, initiator.id, enforced=True)
    initiator.is_active = False
    db_session.add(row)
    await db_session.commit()

    pub = await _promote(db_session)

    published = _published(pub)
    row_id = str(row.id)
    assert row_id not in published
    await db_session.refresh(row)
    assert row.status == ExecutionStatus.FAILED
    assert row.error_message == "Run As is no longer permitted: The user who scheduled this run is inactive"
    [audit] = await _run_as_audit(db_session, initiator.id)
    assert audit.details is not None
    stopped = [(step["key"], step["reason"]) for step in audit.details["trace"]["steps"] if step["status"] == "stopped"]
    assert (audit.outcome, audit.organization_id, audit.details["enforced"], stopped) == (
        "failure",
        colleague.organization_id,
        True,
        [("run_user", "inactive")],
    )


async def _missing_run_user_gaps(db_session) -> int:
    from sqlalchemy import func, select

    from src.jobs.schedulers.deferred_execution_promoter import RECHECK_OPERATION
    from src.models.orm.audit import AuditLog

    query = select(func.count()).where(
        AuditLog.action == "access.check_gap",
        AuditLog.resource_type == "run_as",
        AuditLog.operation_id == RECHECK_OPERATION,
    )
    return (await db_session.execute(query)).scalar_one()


@pytest.mark.asyncio
async def test_a_run_as_whose_initiator_no_longer_exists_fails(db_session):
    _org, colleague = await _contoso_person(db_session)
    row = _acting_as(colleague.id, uuid4(), enforced=True)
    db_session.add(row)
    await db_session.commit()
    gaps_before = await _missing_run_user_gaps(db_session)

    pub = await _promote(db_session)

    # The writer records a decision whose person no longer exists as a gap.
    gaps_after = await _missing_run_user_gaps(db_session)
    assert gaps_after == gaps_before + 1

    published = _published(pub)
    row_id = str(row.id)
    assert row_id not in published
    await db_session.refresh(row)
    assert row.status == ExecutionStatus.FAILED
    assert row.error_message == "Run As is no longer permitted: The user who scheduled this run no longer exists"


@pytest.mark.asyncio
async def test_a_run_as_still_permitted_is_published_as_the_target(db_session):
    initiator, colleague, _role = await _impersonation(db_session)
    row = _acting_as(colleague.id, initiator.id, enforced=True)
    db_session.add(row)
    await db_session.commit()

    pub = await _promote(db_session)

    calls = [
        call.args[0].pending
        for call in pub.await_args_list
        if call.args[0].pending["execution_id"] == str(row.id)
    ]
    [pending] = calls
    colleague_id = str(colleague.id)
    assert pending["user_id"] == colleague_id
    await db_session.refresh(row)
    assert row.status == ExecutionStatus.PENDING
    # Promotion rechecks and records the live named decision, including success.
    [audit] = await _run_as_audit(db_session, initiator.id)
    assert audit.details is not None
    assert (audit.action, audit.outcome, audit.organization_id) == (
        "access.check",
        "success",
        colleague.organization_id,
    )
    assert (audit.details["enforced"], audit.details["inputs"]["run_as_user_id"]) == (
        True,
        colleague_id,
    )


async def _missing_run_as_user_gaps(db_session, requested) -> list:
    from sqlalchemy import select

    from src.jobs.schedulers.deferred_execution_promoter import RECHECK_OPERATION
    from src.models.orm.audit import AuditLog

    query = select(AuditLog).where(
        AuditLog.action == "access.check_gap",
        AuditLog.resource_type == "run_as",
        AuditLog.operation_id == RECHECK_OPERATION,
    )
    rows = (await db_session.execute(query)).scalars().all()
    return [row for row in rows if (row.details or {}).get("run_as_user_id") == str(requested)]


@pytest.mark.asyncio
async def test_a_run_as_whose_target_was_deleted_fails_and_is_recorded(db_session):
    from sqlalchemy import delete

    from src.models.orm.users import User

    initiator, colleague, _role = await _impersonation(db_session)
    row = _acting_as(colleague.id, initiator.id, enforced=True)
    db_session.add(row)
    await db_session.commit()
    row.executed_by = None
    await db_session.execute(delete(User).where(User.id == colleague.id))
    await db_session.commit()

    pub = await _promote(db_session)

    published = _published(pub)
    row_id = str(row.id)
    assert row_id not in published
    await db_session.refresh(row)
    assert row.status == ExecutionStatus.FAILED
    assert row.error_message == f"Run As is no longer permitted: Run As user '{colleague.id}' not found"
    # Recorded as a gap naming the requested user, at the initiator's organization.
    [gap] = await _missing_run_as_user_gaps(db_session, colleague.id)
    gap_facts = (gap.user_id, gap.organization_id, gap.details["reason"])
    assert gap_facts == (initiator.id, initiator.organization_id, "run_as_user_missing")


@pytest.mark.asyncio
async def test_a_workflow_run_as_rechecks_original_reach_and_current_workflow_powers(db_session):
    from src.models.contracts.workflow_permissions import WorkflowPermissionMode
    from src.models.orm.workflows import Workflow
    from src.models.orm.users import User

    org, run_user = await _contoso_person(db_session)
    colleague = User(email=f"{uuid4().hex[:8]}@contoso.example", name="Contoso Colleague", organization_id=org.id)
    departed = User(
        email=f"{uuid4().hex[:8]}@contoso.example",
        name="Departed Contoso Colleague",
        organization_id=org.id,
        is_active=False,
    )
    workflow = Workflow(
        id=uuid4(),
        name=f"run-as-{uuid4().hex[:8]}",
        function_name="run",
        path=f"workflows/run_as_{uuid4().hex[:8]}.py",
        organization_id=org.id,
        permission_mode=WorkflowPermissionMode.FULL,
    )
    db_session.add_all([colleague, departed, workflow])
    await db_session.flush()
    allowed = _acting_as(colleague.id, run_user.id, enforced=True)
    refused = _acting_as(departed.id, run_user.id, enforced=True)
    for row in (allowed, refused):
        row.execution_context["run_as"]["workflow_id"] = str(workflow.id)
    db_session.add_all([allowed, refused])
    await db_session.commit()

    pub = await _promote(db_session)

    published = _published(pub)
    allowed_id, refused_id = str(allowed.id), str(refused.id)
    assert allowed_id in published
    assert refused_id not in published
    await db_session.refresh(refused)
    assert refused.status == ExecutionStatus.FAILED
    expected = f"Run As is no longer permitted: Run As user '{departed.id}' is inactive"
    assert refused.error_message == expected
    audits = await _run_as_audit(db_session, run_user.id)
    by_target = {
        audit.details["inputs"]["run_as_user_id"]: audit
        for audit in audits
        if audit.details is not None
    }
    assert set(by_target) == {str(colleague.id), str(departed.id)}
    assert by_target[str(colleague.id)].outcome == "success"
    audit = by_target[str(departed.id)]
    assert audit.details is not None
    stopped = [(step["key"], step["reason"]) for step in audit.details["trace"]["steps"] if step["status"] == "stopped"]
    recorded = (audit.outcome, audit.details["enforced"], audit.details["inputs"]["run_as_user_id"], stopped)
    assert recorded == ("failure", True, str(departed.id), [("run_as_user", "inactive")])


@pytest.mark.asyncio
async def test_a_workflow_run_as_whose_target_was_deleted_is_recorded(db_session):
    _org, run_user = await _contoso_person(db_session)
    departed = uuid4()
    row = _acting_as(departed, run_user.id, enforced=True)
    row.executed_by = None
    db_session.add(row)
    await db_session.commit()

    pub = await _promote(db_session)

    published = _published(pub)
    row_id = str(row.id)
    assert row_id not in published
    await db_session.refresh(row)
    assert row.error_message == f"Run As is no longer permitted: Run As user '{departed}' not found"
    [gap] = await _missing_run_as_user_gaps(db_session, departed)
    assert (gap.user_id, gap.details["reason"]) == (run_user.id, "run_as_user_missing")


@pytest.mark.asyncio
async def test_a_row_without_run_as_is_published_as_before(db_session):
    plain = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    plain.execution_context = {"is_platform_admin": True}
    db_session.add(plain)
    await db_session.commit()

    pub = await _promote(db_session)

    calls = [
        call.args[0]
        for call in pub.await_args_list
        if call.args[0].pending["execution_id"] == str(plain.id)
    ]
    [accepted] = calls
    pending = dict(accepted.pending)
    created_at = pending.pop("created_at")
    assert created_at
    assert pending == {
        "execution_id": str(plain.id),
        "workflow_id": None,
        "script_name": None,
        "parameters": {"k": 1},
        "org_id": None,
        "user_id": "",
        "user_name": "user",
        "user_email": "",
        "form_id": None,
        "startup": None,
        "form_inputs": {},
        "embed": {},
        "api_key_id": None,
        "sync": False,
        "is_platform_admin": True,
        "event": None,
        "artifact_workspace_id": None,
        "lineage": None,
        "impersonation_provenance": None,
    }
    await db_session.refresh(plain)
    assert (plain.status, plain.error_message, plain.completed_at) == (ExecutionStatus.PENDING, None, None)


@pytest.mark.asyncio
async def test_an_initiator_deleted_during_the_recheck_fails_that_row_only(db_session):
    from sqlalchemy.exc import NoResultFound

    from src.services.authorization import explain

    initiator, colleague, _role = await _impersonation(db_session)
    other_initiator, other_colleague, _other_role = await _impersonation(db_session)
    orphaned = _acting_as(colleague.id, initiator.id, enforced=True)
    permitted = _acting_as(other_colleague.id, other_initiator.id, enforced=True)
    plain = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    db_session.add_all([orphaned, permitted, plain])
    await db_session.commit()
    real_context = explain.build_authorization_context

    async def deleted_meanwhile(db, user_id):
        # The initiator is found, then gone by the time their roles are read.
        if user_id == initiator.id:
            raise NoResultFound()
        return await real_context(db, user_id)

    with patch.object(explain, "build_authorization_context", deleted_meanwhile):
        pub = await _promote(db_session)

    published = _published(pub)
    orphaned_id, permitted_id, plain_id = str(orphaned.id), str(permitted.id), str(plain.id)
    assert orphaned_id not in published
    assert {permitted_id, plain_id} <= published
    await db_session.refresh(orphaned)
    assert orphaned.status == ExecutionStatus.FAILED
    assert orphaned.error_message == "Run As is no longer permitted: The user who scheduled this run no longer exists"
