"""Browser impersonation sessions preserve target identity and initiator provenance."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from shared import access_checks
from src.core.principal import UserPrincipal
from src.core.security import decode_token
from src.core.cache import get_shared_redis
from src.core.cache.keys import TTL_REFRESH_TOKEN, refresh_token_jti_key
from src.models.orm.organizations import Organization
from src.models.orm.users import Role, RolePermission, User, UserRole, UserRoleBoundary
from src.services.authorization.impersonation import RunAsError


@pytest_asyncio.fixture(autouse=True)
async def _fresh_shared_redis_client(monkeypatch):
    from src.core.cache import redis_client
    from src.core.cache.redis_client import close_shared_redis

    monkeypatch.setattr(redis_client, "_shared_client", None)
    yield
    await close_shared_redis()


async def _user(session: AsyncSession, organization: Organization, *, active: bool = True) -> User:
    user = User(
        email=f"{uuid4().hex}@example.test",
        name="Test User",
        organization_id=organization.id,
        is_active=active,
    )
    session.add(user)
    await session.flush()
    return user


async def _grant_impersonate(session: AsyncSession, user: User, organization: Organization) -> None:
    role = Role(name=f"Impersonator {uuid4().hex}", created_by="browser-impersonation-test")
    session.add(role)
    await session.flush()
    session.add_all(
        [
            RolePermission(role_id=role.id, permission="users.impersonate"),
            UserRole(user_id=user.id, role_id=role.id, assigned_by="browser-impersonation-test"),
            UserRoleBoundary(
                user_id=user.id,
                role_id=role.id,
                kind="organization",
                organization_id=organization.id,
            ),
        ]
    )
    await session.flush()


def _principal(user: User) -> UserPrincipal:
    return UserPrincipal(user_id=user.id, email=user.email, organization_id=user.organization_id)


@pytest.mark.asyncio
async def test_browser_session_is_provenanced_and_revocable(db_session: AsyncSession) -> None:
    from shared.browser_impersonation import (
        create_browser_impersonation,
        revoke_browser_impersonation,
        validate_browser_impersonation,
    )

    organization = Organization(name=f"Impersonation {uuid4().hex}", created_by="browser-impersonation-test")
    db_session.add(organization)
    await db_session.flush()
    initiator = await _user(db_session, organization)
    target = await _user(db_session, organization)
    await _grant_impersonate(db_session, initiator, organization)

    refresh_jti = str(uuid4())
    redis = await get_shared_redis()
    await redis.setex(refresh_token_jti_key(str(initiator.id), refresh_jti), TTL_REFRESH_TOKEN, "1")
    credential = await create_browser_impersonation(
        db_session, _principal(initiator), target.id, initiator_refresh_jti=refresh_jti
    )
    claims = decode_token(credential.access_token, expected_type="access")

    assert claims is not None
    assert claims["sub"] == str(target.id)
    assert claims["impersonated_by_user_id"] == str(initiator.id)
    assert UUID(claims["impersonation_session_id"])

    impersonated = UserPrincipal(
        user_id=target.id,
        email=target.email,
        organization_id=target.organization_id,
        impersonated_by_user_id=UUID(claims["impersonated_by_user_id"]),
        impersonation_session_id=UUID(claims["impersonation_session_id"]),
    )
    token = access_checks.collect_person(target.id)
    try:
        await validate_browser_impersonation(db_session, impersonated)

        collector = access_checks.current()
        assert collector is not None
        assert collector.notes == []
    finally:
        access_checks.stop_collecting(token)

    await revoke_browser_impersonation(impersonated)
    with pytest.raises(PermissionError, match="session is no longer active"):
        await validate_browser_impersonation(db_session, impersonated)


@pytest.mark.asyncio
async def test_browser_session_requires_current_impersonation_permission(db_session: AsyncSession) -> None:
    from shared.browser_impersonation import (
        BrowserImpersonationError,
        create_browser_impersonation,
        validate_browser_impersonation,
    )

    organization = Organization(name=f"Impersonation {uuid4().hex}", created_by="browser-impersonation-test")
    db_session.add(organization)
    await db_session.flush()
    initiator = await _user(db_session, organization)
    target = await _user(db_session, organization)

    with pytest.raises(RunAsError, match="permission to run as"):
        await create_browser_impersonation(
            db_session, _principal(initiator), target.id, initiator_refresh_jti=str(uuid4())
        )

    await _grant_impersonate(db_session, initiator, organization)
    refresh_jti = str(uuid4())
    redis = await get_shared_redis()
    await redis.setex(refresh_token_jti_key(str(initiator.id), refresh_jti), TTL_REFRESH_TOKEN, "1")
    credential = await create_browser_impersonation(
        db_session, _principal(initiator), target.id, initiator_refresh_jti=refresh_jti
    )
    claims = decode_token(credential.access_token, expected_type="access")
    assert claims is not None
    principal = UserPrincipal(
        user_id=target.id,
        email=target.email,
        organization_id=target.organization_id,
        impersonated_by_user_id=UUID(claims["impersonated_by_user_id"]),
        impersonation_session_id=UUID(claims["impersonation_session_id"]),
    )

    await db_session.execute(delete(RolePermission).where(RolePermission.permission == "users.impersonate"))
    await db_session.flush()

    token = access_checks.collect_person(target.id)
    try:
        with pytest.raises(BrowserImpersonationError, match="permission to run as"):
            await validate_browser_impersonation(db_session, principal)

        collector = access_checks.current()
        assert collector is not None
        assert collector.notes == []
        access_checks.note("scope_switch", None, name="revalidation-finished")
        assert collector.notes == [
            access_checks.Note("scope_switch", None, {"name": "revalidation-finished"})
        ]
    finally:
        access_checks.stop_collecting(token)
