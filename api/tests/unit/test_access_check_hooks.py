"""Shared resolvers note the target a run's request acts in."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from shared import access_checks
from shared.identities import ensure_default_identity
import shared.scope_resolver as scope_resolver
from src.services.authorization.reach import OrgReach
from src.services.authorization.context import AuthorizationContext
from src.services.authorization.enforce import Caller
from shared.builtin_roles import USER_ROLE_ID
from src.core.principal import UserPrincipal
from src.models.orm.organizations import Organization
from src.models.orm.users import User
from src.models.orm.workflows import Workflow
from src.repositories.workflows import WorkflowRepository
from src.services.access_check_entry import run_user_may_open

@pytest.fixture
def collector():
    token = access_checks.collect_run(uuid4(), uuid4(), uuid4())
    try:
        yield access_checks.current()
    finally:
        access_checks.stop_collecting(token)


def _targets(collector) -> list:
    return [(note.kind, note.target) for note in collector.notes]


def test_resolve_effective_scope_notes_the_resolved_scope(collector, monkeypatch) -> None:
    org = uuid4()
    monkeypatch.setattr(scope_resolver, "user_reach", lambda caller: OrgReach(organization_ids=frozenset({org}), include_global=True))
    user_id = uuid4()
    caller = Caller(
        UserPrincipal(user_id=user_id, email="person@contoso.example", organization_id=org, is_active=True),
        AuthorizationContext(
            user_id=user_id, home_organization_id=org, base_role_id=USER_ROLE_ID,
            is_external=False, base_permissions=frozenset(),
        ),
    )

    scope_resolver.resolve_effective_scope(caller=caller, requested_scope=org)
    scope_resolver.resolve_effective_scope(caller=caller, requested_scope=scope_resolver.UNSET)

    assert _targets(collector) == [("scope_switch", org), ("scope_switch", org)]


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
    assert await opens(admin.id) is False  # A copied flag supplies no role authority.
    from shared.sdk_users import set_platform_admin

    await set_platform_admin(db_session, admin, True, assigned_by="entry-test")
    assert await opens(admin.id) is True
