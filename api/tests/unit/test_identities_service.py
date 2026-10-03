"""Identities: finding and creating them, and which workflows may name them.

DB-backed: the migrated test database already holds the global identity and
a default identity for the provider organization.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.builtin_roles import USER_ROLE_ID
from shared.identities import (
    IDENTITY_EMAIL_DOMAIN,
    IdentityKind,
    default_identity,
    ensure_default_identity,
    is_identity,
    is_identity_email,
    run_identity_allowed,
)
from src.core.constants import PROVIDER_ORG_ID
from src.models.orm.organizations import Organization
from src.models.orm.users import User


@pytest_asyncio.fixture
async def db_session(async_engine):
    async with async_engine.connect() as connection:
        transaction = await connection.begin()
        async with AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        ) as session:
            try:
                yield session
            finally:
                await session.rollback()
                await transaction.rollback()


async def _org(session: AsyncSession, name: str) -> Organization:
    org = Organization(name=name, created_by="identities-test")
    session.add(org)
    await session.flush()
    return org


def _identity(organization_id, kind: IdentityKind = IdentityKind.ORG_DEFAULT) -> User:
    return User(email=f"{uuid4()}@{IDENTITY_EMAIL_DOMAIN}", organization_id=organization_id, identity_kind=kind)


@pytest.mark.asyncio
async def test_ensure_default_identity_creates_one_ordinary_account(db_session: AsyncSession) -> None:
    org = await _org(db_session, "Identity Test Org")

    identity = await ensure_default_identity(db_session, org)

    assert identity.email == f"identity-{org.id}@{IDENTITY_EMAIL_DOMAIN}"
    assert identity.name == "Identity Test Org identity"
    assert identity.identity_kind == IdentityKind.ORG_DEFAULT
    assert identity.organization_id == org.id
    assert identity.base_role_id == USER_ROLE_ID
    assert identity.hashed_password is None
    assert (identity.is_active, identity.is_verified, identity.is_registered) == (True, True, True)
    assert (identity.is_superuser, identity.is_system, identity.is_external) == (False, False, False)

    again = await ensure_default_identity(db_session, org)
    assert again.id == identity.id
    count = await db_session.scalar(
        select(func.count(User.id)).where(
            User.organization_id == org.id, User.identity_kind == IdentityKind.ORG_DEFAULT
        )
    )
    assert count == 1


@pytest.mark.asyncio
async def test_default_identity_finds_the_global_and_organization_identities(db_session: AsyncSession) -> None:
    global_identity = await default_identity(db_session, None)
    assert global_identity.identity_kind == IdentityKind.GLOBAL_DEFAULT
    assert global_identity.organization_id is None

    provider_identity = await default_identity(db_session, PROVIDER_ORG_ID)
    assert provider_identity.identity_kind == IdentityKind.ORG_DEFAULT
    assert provider_identity.organization_id == PROVIDER_ORG_ID
    assert provider_identity.is_superuser is True


def test_run_identity_allowed() -> None:
    contoso, fabrikam = uuid4(), uuid4()
    person = User(email="person@example.test", organization_id=PROVIDER_ORG_ID)
    cases = [
        (contoso, _identity(contoso), True),
        (contoso, _identity(fabrikam), False),
        (contoso, _identity(PROVIDER_ORG_ID), False),
        (contoso, _identity(None, IdentityKind.GLOBAL_DEFAULT), False),
        (None, _identity(PROVIDER_ORG_ID), True),
        (None, _identity(None, IdentityKind.GLOBAL_DEFAULT), True),
        (None, _identity(contoso), False),
        (PROVIDER_ORG_ID, _identity(None, IdentityKind.GLOBAL_DEFAULT), True),
        (PROVIDER_ORG_ID, _identity(PROVIDER_ORG_ID, IdentityKind.CUSTOM), True),
        (PROVIDER_ORG_ID, person, False),
    ]
    for workflow_org, identity, expected in cases:
        assert (
            run_identity_allowed(workflow_organization_id=workflow_org, identity=identity) is expected
        ), (workflow_org, identity.organization_id, identity.identity_kind)


def test_identity_facts() -> None:
    assert is_identity(_identity(None, IdentityKind.GLOBAL_DEFAULT))
    assert not is_identity(User(email="person@example.test"))
    assert is_identity_email(f"identity-x@{IDENTITY_EMAIL_DOMAIN}")
    assert is_identity_email(f"Someone@{IDENTITY_EMAIL_DOMAIN.upper()}")
    assert not is_identity_email("identity-x@example.test")
    assert not is_identity_email(f"identity-x@sub.{IDENTITY_EMAIL_DOMAIN}.example.test")

