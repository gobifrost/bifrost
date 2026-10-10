"""Unit tests for the deferred execution promoter."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio

from src.models.enums import ExecutionStatus
from src.models.orm.executions import Execution


PATH_PUBLISH = "src.jobs.schedulers.deferred_execution_promoter._publish_pending"
PATH_DB_CTX = "src.jobs.schedulers.deferred_execution_promoter.get_db_context"


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
        patch(PATH_PUBLISH, new=AsyncMock()) as pub,
    ):
        promoted, failed = await promote_due_executions()

    assert promoted == 1
    assert failed == 0
    pub.assert_awaited_once()
    assert pub.await_args.kwargs["execution_record_exists"] is True

    await db_session.refresh(due)
    assert due.status == ExecutionStatus.PENDING


@pytest.mark.asyncio
async def test_leaves_future_rows(db_session):
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    future = _new_scheduled(datetime.now(timezone.utc) + timedelta(hours=1))
    db_session.add(future)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock()) as pub,
    ):
        promoted, failed = await promote_due_executions()

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
        patch(PATH_PUBLISH, new=AsyncMock()),
    ):
        promoted, _ = await promote_due_executions()

    assert promoted == 0
    await db_session.refresh(cancelled)
    assert cancelled.status == ExecutionStatus.CANCELLED


@pytest.mark.asyncio
async def test_reverts_on_publish_failure(db_session):
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    due = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    db_session.add(due)
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock(side_effect=RuntimeError("rabbit down"))),
    ):
        promoted, failed = await promote_due_executions()

    assert promoted == 0
    assert failed == 1

    await db_session.refresh(due)
    # Reverted so next tick can retry.
    assert due.status == ExecutionStatus.SCHEDULED


@pytest.mark.asyncio
async def test_forwards_the_row_lineage(db_session):
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
    plain = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    db_session.add_all([due, plain])
    await db_session.commit()

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock()) as pub,
    ):
        await promote_due_executions()

    lineage = {call.kwargs["execution_id"]: call.kwargs["lineage"] for call in pub.await_args_list}
    assert lineage[str(due.id)] == {
        "run_user_id": str(identity),
        "started_by_user_id": str(identity),
        "root_execution_id": str(due.id),
    }
    assert lineage[str(plain.id)] is None


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


def _acting_as(target_id, authorized_by, *, enforced: bool) -> Execution:
    row = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    row.executed_by = target_id
    row.execution_context = {
        "is_platform_admin": False,
        "run_as": {"user_id": str(target_id), "authorized_by": str(authorized_by), "enforced": enforced},
    }
    return row


async def _promote(db_session) -> AsyncMock:
    from src.jobs.schedulers.deferred_execution_promoter import promote_due_executions

    with (
        patch(PATH_DB_CTX, return_value=_DbCtx(db_session)),
        patch(PATH_PUBLISH, new=AsyncMock()) as pub,
    ):
        await promote_due_executions()
    return pub


def _published(pub: AsyncMock) -> set[str]:
    return {call.kwargs["execution_id"] for call in pub.await_args_list}


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

    calls = [call.kwargs for call in pub.await_args_list if call.kwargs["execution_id"] == str(row.id)]
    [kwargs] = calls
    colleague_id = str(colleague.id)
    assert kwargs["user_id"] == colleague_id
    await db_session.refresh(row)
    assert row.status == ExecutionStatus.PENDING
    # Only a refusal is recorded; the decision when it was scheduled already was.
    audit = await _run_as_audit(db_session, initiator.id)
    assert audit == []


@pytest.mark.asyncio
async def test_a_workflow_run_as_checks_the_target_only(db_session):
    from src.core.constants import SYSTEM_USER_UUID

    # The engine account holds no grant; a workflow's Run As is not judged on roles here.
    _org, colleague = await _contoso_person(db_session)
    _org2, departed = await _contoso_person(db_session, is_active=False)
    allowed = _acting_as(colleague.id, SYSTEM_USER_UUID, enforced=False)
    refused = _acting_as(departed.id, SYSTEM_USER_UUID, enforced=False)
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


@pytest.mark.asyncio
async def test_a_row_without_run_as_is_published_as_before(db_session):
    plain = _new_scheduled(datetime.now(timezone.utc) - timedelta(seconds=1))
    plain.execution_context = {"is_platform_admin": True}
    db_session.add(plain)
    await db_session.commit()

    pub = await _promote(db_session)

    calls = [call.kwargs for call in pub.await_args_list if call.kwargs["execution_id"] == str(plain.id)]
    [kwargs] = calls
    assert kwargs == {
        "execution_id": str(plain.id),
        "workflow_id": None,
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
        "file_path": None,
        "execution_record_exists": True,
        "lineage": None,
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
