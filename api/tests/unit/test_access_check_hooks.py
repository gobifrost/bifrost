"""Shared resolvers note the target a run's request acts in."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from shared import access_checks
from shared.identities import ensure_default_identity
from shared.scope_resolver import UNSET, resolve_effective_scope
from src.core.org_filter import resolve_org_filter, resolve_target_org
from src.core.principal import UserPrincipal
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.models.orm.workflows import Workflow
from src.repositories.workflows import WorkflowRepository
from src.services.access_check_entry import run_user_may_open

ENGINE = UserPrincipal(user_id=uuid4(), email="engine@x.example", organization_id=None, is_superuser=True)


@pytest.fixture
def collector():
    token = access_checks.start_collecting(
        {"sub": str(uuid4()), "is_superuser": True, "engine_execution_id": str(uuid4()), "engine_run_user_id": str(uuid4())}
    )
    try:
        yield access_checks.current()
    finally:
        access_checks.stop_collecting(token)


def _targets(collector) -> list:
    return [(note.kind, note.target) for note in collector.notes]


def test_resolve_target_org_notes_where_the_write_lands(collector) -> None:
    org, default = uuid4(), uuid4()

    assert resolve_target_org(ENGINE, str(org), default) == org
    assert resolve_target_org(ENGINE, "global", default) is None
    assert resolve_target_org(ENGINE, None, default) == default

    assert _targets(collector) == [("scope_switch", org), ("scope_switch", None), ("scope_switch", default)]


def test_resolve_effective_scope_notes_the_resolved_scope(collector) -> None:
    org = uuid4()

    resolve_effective_scope(caller_org_id=None, is_platform_admin=True, requested_scope=org)
    resolve_effective_scope(caller_org_id=org, is_platform_admin=True, requested_scope=UNSET)

    assert _targets(collector) == [("scope_switch", org), ("scope_switch", org)]


def test_a_list_with_no_scope_targets_every_org(collector) -> None:
    resolve_org_filter(ENGINE, None)
    resolve_org_filter(ENGINE, "global")

    assert _targets(collector) == [("scope_switch", "*"), ("scope_switch", None)]


def test_resolvers_note_nothing_outside_a_run() -> None:
    resolve_target_org(ENGINE, str(uuid4()), None)
    assert access_checks.current() is None


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


async def test_a_run_user_opens_only_what_their_own_access_allows(db_session: AsyncSession) -> None:
    org = Organization(name=f"Entry Org {uuid4().hex[:8]}", created_by="entry-test")
    db_session.add(org)
    await db_session.flush()
    await ensure_default_identity(db_session, org)
    person = User(email=f"{uuid4()}@entry.example", name="Person", organization_id=org.id)
    admin = User(email=f"{uuid4()}@entry.example", name="Admin", organization_id=org.id, is_superuser=True)
    name = f"entry_{uuid4().hex[:8]}"
    workflow = Workflow(name=name, function_name=name, path=f"workflows/{name}.py", organization_id=org.id)
    db_session.add_all([person, admin, workflow])
    await db_session.flush()

    async def opens(user_id: UUID) -> bool:
        return await run_user_may_open(db_session, WorkflowRepository, user_id, workflow.id)

    assert await opens(person.id) is False  # role_based, no role
    assert await opens(admin.id) is True
