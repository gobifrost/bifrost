"""``authorize_run_as``: who may run a workflow or an agent as another user."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.util import EMPTY_DICT

from shared import access_checks
from shared.identities import ensure_default_identity
from shared.sdk_users import set_platform_admin
from src.core.cache.redis_client import close_shared_redis
from src.core.constants import PROVIDER_ORG_ID, SYSTEM_USER_UUID
from src.core.principal import UserPrincipal
from src.models.enums import IdentityKind
from src.models.orm.audit import AuditLog
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.services.access_check_writer import flush
from src.services.authorization.impersonation import RunAsError, authorize_run_as


@pytest_asyncio.fixture(autouse=True)
async def _fresh_redis_client(monkeypatch):
    # The shared client binds to the event loop that created it; each test
    # runs in its own loop, so it starts without one and closes the one it made.
    from src.core.cache import redis_client

    monkeypatch.setattr(redis_client, "_shared_client", None)
    yield
    await close_shared_redis()


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


class _CountingSession:
    """The real session, recording every statement ``execute`` runs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.statements: list[Any] = []

    async def execute(self, statement, params=None, *, execution_options=EMPTY_DICT, bind_arguments=None):
        self.statements.append(statement)
        return await self._session.execute(
            statement, params, execution_options=execution_options, bind_arguments=bind_arguments
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    def looked_up(self, user_id: UUID) -> bool:
        return any(user_id in statement.compile().params.values() for statement in self.statements)


async def _org(session: AsyncSession, name: str) -> Organization:
    org = Organization(name=f"{name} {uuid4().hex[:8]}", created_by="impersonation-test")
    session.add(org)
    await session.flush()
    return org


async def _user(session: AsyncSession, org: Organization, **fields: Any) -> User:
    user = User(email=f"{uuid4().hex[:8]}@contoso.example", name="Contoso Person", organization_id=org.id, **fields)
    session.add(user)
    await session.flush()
    return user


async def _grant(session: AsyncSession, user: User, permission: str, at: Organization) -> Role:
    role = Role(name=f"Role {uuid4().hex[:8]}", created_by="impersonation-test")
    session.add(role)
    await session.flush()
    session.add_all(
        [
            RolePermission(role_id=role.id, permission=permission),
            UserRole(user_id=user.id, role_id=role.id, assigned_by="impersonation-test"),
        ]
    )
    await session.flush()
    session.add(UserRoleBoundary(user_id=user.id, role_id=role.id, kind="organization", organization_id=at.id))
    await session.flush()
    return role


def _person(user: User) -> UserPrincipal:
    return UserPrincipal(user_id=user.id, email=user.email, organization_id=user.organization_id)


def _engine(run_user_id: UUID | None) -> UserPrincipal:
    return UserPrincipal(
        user_id=SYSTEM_USER_UUID,
        email="system@internal.gobifrost.com",
        organization_id=None,
        is_superuser=True,
        engine_execution_id=str(uuid4()),
        run_user_id=run_user_id,
    )


@contextmanager
def _collecting(user_id: UUID) -> Iterator[access_checks.Collector]:
    token = access_checks.collect_person(user_id)
    collector = access_checks.current()
    assert collector is not None
    try:
        yield collector
    finally:
        access_checks.stop_collecting(token)


async def _status(db: AsyncSession, principal: UserPrincipal, user_id: UUID) -> int:
    with pytest.raises(RunAsError) as exc_info:
        await authorize_run_as(db, principal, user_id)
    return exc_info.value.status_code


async def _contoso_impersonator(db: AsyncSession) -> tuple[Organization, Organization, User]:
    contoso, fabrikam = await _org(db, "Contoso"), await _org(db, "Fabrikam")
    holder = await _user(db, contoso)
    await _grant(db, holder, "users.impersonate", contoso)
    return contoso, fabrikam, holder


async def test_a_person_without_the_permission_is_refused_and_recorded_before_any_user_is_looked_up(
    db_session: AsyncSession,
) -> None:
    contoso = await _org(db_session, "Contoso")
    person, colleague = await _user(db_session, contoso), await _user(db_session, contoso)
    counting = _CountingSession(db_session)

    with _collecting(person.id) as collector:
        status = await _status(cast(AsyncSession, counting), _person(person), colleague.id)
    await flush(cast(AsyncSession, counting), collector, operation="POST /api/workflows/execute", route=None)

    assert status == 403
    looked_up = counting.looked_up(colleague.id)
    assert looked_up is False
    rows = (await db_session.execute(select(AuditLog).where(AuditLog.user_id == person.id))).scalars().all()
    [row] = rows
    assert (row.action, row.outcome, row.resource_type, row.organization_id) == (
        "access.check",
        "failure",
        "run_as",
        contoso.id,
    )
    assert row.details is not None
    requested = str(colleague.id)
    assert (row.details["enforced"], row.details["inputs"]["run_as_user_id"]) == (True, requested)
    stopped = [(step["key"], step["facts"]) for step in row.details["trace"]["steps"] if step["status"] == "stopped"]
    assert stopped == [
        ("permission", {"permission": "users.impersonate", "permission_display_name": "Impersonate Users"})
    ]


async def test_a_grant_at_contoso_covers_contoso_users_only(db_session: AsyncSession) -> None:
    contoso, fabrikam, holder = await _contoso_impersonator(db_session)
    in_contoso, in_fabrikam = await _user(db_session, contoso), await _user(db_session, fabrikam)
    counting = _CountingSession(db_session)

    with _collecting(holder.id) as collector:
        target = await authorize_run_as(cast(AsyncSession, counting), _person(holder), in_contoso.id)
        status = await _status(db_session, _person(holder), in_fabrikam.id)

    assert target is not None and target.user_id == in_contoso.id
    assert status == 403
    looked_up = counting.looked_up(in_contoso.id)
    assert looked_up is True
    run_as = [(note.target, note.facts) for note in collector.notes if note.kind == "run_as"]
    assert run_as == [
        (contoso.id, {"run_as_user_id": in_contoso.id, "enforced": True}),
        (fabrikam.id, {"run_as_user_id": in_fabrikam.id, "enforced": True}),
    ]
    powers = [(note.target, note.facts["permission"]) for note in collector.notes if note.kind == "permission"]
    assert powers == [(contoso.id, "users.impersonate"), (fabrikam.id, "users.impersonate")]


async def test_an_unknown_user_is_not_found(db_session: AsyncSession) -> None:
    _contoso, _fabrikam, holder = await _contoso_impersonator(db_session)
    unknown = uuid4()

    with pytest.raises(RunAsError) as exc_info:
        await authorize_run_as(db_session, _person(holder), unknown)

    assert (exc_info.value.status_code, exc_info.value.detail) == (404, f"Run As user '{unknown}' not found")


@pytest.mark.parametrize("kind", ["inactive", "identity", "system"])
async def test_only_an_active_person_can_be_acted_as(db_session: AsyncSession, kind: str) -> None:
    contoso, _fabrikam, holder = await _contoso_impersonator(db_session)
    if kind == "inactive":
        target_id = (await _user(db_session, contoso, is_active=False)).id
    elif kind == "identity":
        await ensure_default_identity(db_session, contoso)
        target_id = (
            await db_session.execute(
                select(User.id).where(User.identity_kind == IdentityKind.ORG_DEFAULT, User.organization_id == contoso.id)
            )
        ).scalar_one()
    else:
        target_id = SYSTEM_USER_UUID

    with _collecting(holder.id) as collector:
        status = await _status(db_session, _person(holder), target_id)

    assert status == 400
    kinds = [note.kind for note in collector.notes]
    assert "run_as" in kinds


async def test_a_privileged_user_needs_a_platform_admin(db_session: AsyncSession) -> None:
    contoso, _fabrikam, holder = await _contoso_impersonator(db_session)
    privileged = await _user(db_session, contoso)
    await _grant(db_session, privileged, "users.readwrite", contoso)
    admin = User(email=f"{uuid4().hex[:8]}@provider.example", name="Admin", organization_id=PROVIDER_ORG_ID)
    db_session.add(admin)
    await db_session.flush()
    await set_platform_admin(db_session, admin, True, assigned_by="impersonation-test")

    status = await _status(db_session, _person(holder), privileged.id)
    with _collecting(admin.id) as collector:
        target = await authorize_run_as(db_session, _person(admin), privileged.id)

    assert status == 403
    assert target is not None and target.privileged
    powers = [note.facts["permission"] for note in collector.notes if note.kind == "permission"]
    assert powers == ["users.impersonate", "privilegedaccess.readwrite"]


async def test_naming_yourself_is_not_impersonation(db_session: AsyncSession) -> None:
    contoso = await _org(db_session, "Contoso")
    person = await _user(db_session, contoso)

    with _collecting(person.id) as collector:
        mine = await authorize_run_as(db_session, _person(person), person.id)
        runs = await authorize_run_as(db_session, _engine(person.id), person.id)

    assert (mine, runs) == (None, None)
    assert collector.notes == []


async def test_a_workflow_runs_as_a_user_report_only(db_session: AsyncSession) -> None:
    contoso = await _org(db_session, "Contoso")
    run_user, colleague = await _user(db_session, contoso), await _user(db_session, contoso)
    inactive = await _user(db_session, contoso, is_active=False)
    token = access_checks.start_collecting(
        {"engine_execution_id": str(uuid4()), "is_superuser": True, "engine_run_user_id": str(run_user.id)}
    )
    collector = access_checks.current()
    try:
        target = await authorize_run_as(db_session, _engine(run_user.id), colleague.id)
        missing = await _status(db_session, _engine(run_user.id), uuid4())
        unusable = await _status(db_session, _engine(run_user.id), inactive.id)
    finally:
        access_checks.stop_collecting(token)

    assert target is not None and target.user_id == colleague.id
    assert (missing, unusable) == (404, 400)
    assert collector is not None
    run_as = [(note.target, note.facts) for note in collector.notes if note.kind == "run_as"]
    assert run_as == [(contoso.id, {"run_as_user_id": colleague.id})]


async def test_a_service_token_naming_its_own_run_user_is_refused(db_session: AsyncSession) -> None:
    contoso = await _org(db_session, "Contoso")
    run_user = await _user(db_session, contoso)
    service = UserPrincipal(
        user_id=SYSTEM_USER_UUID,
        email="system@internal.gobifrost.com",
        organization_id=None,
        service_id=str(uuid4()),
        run_user_id=run_user.id,
    )

    status = await _status(db_session, service, run_user.id)

    assert status == 403


async def test_service_tokens_and_embedded_sessions_are_refused(db_session: AsyncSession) -> None:
    contoso = await _org(db_session, "Contoso")
    colleague = await _user(db_session, contoso)
    service = UserPrincipal(
        user_id=SYSTEM_USER_UUID,
        email="system@internal.gobifrost.com",
        organization_id=None,
        service_id=str(uuid4()),
        run_user_id=uuid4(),
    )
    embed = UserPrincipal(
        user_id=SYSTEM_USER_UUID,
        email="system@internal.gobifrost.com",
        organization_id=contoso.id,
        is_superuser=True,
        embed=True,
    )

    statuses = (await _status(db_session, service, colleague.id), await _status(db_session, embed, colleague.id))

    assert statuses == (403, 403)


async def test_removing_the_role_revokes_impersonation(db_session: AsyncSession) -> None:
    contoso = await _org(db_session, "Contoso")
    holder, colleague = await _user(db_session, contoso), await _user(db_session, contoso)
    role = await _grant(db_session, holder, "users.impersonate", contoso)

    before = await authorize_run_as(db_session, _person(holder), colleague.id)
    await db_session.execute(delete(UserRole).where(UserRole.user_id == holder.id, UserRole.role_id == role.id))
    await db_session.flush()
    after = await _status(db_session, _person(holder), colleague.id)

    assert before is not None
    assert after == 403
