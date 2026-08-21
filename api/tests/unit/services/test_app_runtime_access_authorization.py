from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.exceptions import AccessDeniedError
from src.core.principal import UserPrincipal
from src.models.orm.applications import Application
from src.models.orm.organizations import Organization
from src.models.orm.solutions import Solution
from src.models.orm.users import User
from src.services.authorization import AuthorizationBoundary, AuthorizationContext
from src.services.solutions import app_runtime_access
from src.services.solutions.app_runtime_access import load_runtime_viewer
from shared.authorization_scopes import PLATFORM_SUPERUSER_SCOPE


def _now():
    return datetime.now(timezone.utc)


async def _shared_runtime_fixture(
    db: AsyncSession,
    *,
    is_superuser: bool,
) -> tuple[User, Solution, Application]:
    org = Organization(
        id=uuid4(),
        name=f"Org {uuid4().hex[:8]}",
        domain=f"{uuid4().hex[:8]}.example.com",
        created_by="test@example.com",
        created_at=_now(),
        updated_at=_now(),
    )
    user = User(
        id=uuid4(),
        email=f"user-{uuid4().hex[:8]}@example.com",
        name="Runtime User",
        is_active=True,
        is_superuser=is_superuser,
        is_verified=True,
        is_registered=True,
        organization_id=org.id,
        created_at=_now(),
        updated_at=_now(),
    )
    solution = Solution(
        id=uuid4(),
        slug=f"runtime-{uuid4().hex[:8]}",
        name="Runtime",
        organization_id=org.id,
        visibility="shared",
        status="active",
    )
    app = Application(
        id=uuid4(),
        name="Runtime App",
        slug=f"runtime-{uuid4().hex[:8]}",
        repo_path="apps/runtime",
        app_model="standalone_v2",
        runtime_mode="isolated",
        organization_id=org.id,
        solution_id=solution.id,
        access_level="role_based",
    )
    db.add_all([org, user, solution, app])
    await db.commit()
    return user, solution, app


def _authorization(user: User, *capabilities: str) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=user.id,
        email=user.email,
        organization_id=user.organization_id,
        name=user.name or user.email,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=AuthorizationBoundary.organization(user.organization_id),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


@pytest.mark.asyncio
async def test_shared_app_runtime_uses_canonical_superuser_capability_when_legacy_bit_false(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    user, solution, app = await _shared_runtime_fixture(
        db_session,
        is_superuser=False,
    )
    captured_bypass: list[bool] = []

    async def fake_resolve_authorization_context(*args, **kwargs):
        return _authorization(user, PLATFORM_SUPERUSER_SCOPE)

    class FakeApplicationRepository:
        def __init__(self, *args, bypass_resource_roles: bool, **kwargs):
            captured_bypass.append(bypass_resource_roles)

        async def can_access(self, **kwargs):
            return True

    monkeypatch.setattr(
        app_runtime_access,
        "resolve_authorization_context",
        fake_resolve_authorization_context,
    )
    monkeypatch.setattr(
        app_runtime_access,
        "ApplicationRepository",
        FakeApplicationRepository,
    )

    viewer = await load_runtime_viewer(
        db_session,
        user_id=user.id,
        solution_id=solution.id,
        app_id=app.id,
        organization_id=solution.organization_id,
    )

    assert viewer is not None
    assert captured_bypass == [True]


@pytest.mark.asyncio
async def test_shared_app_runtime_rejects_stale_legacy_bit_without_capability(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
):
    user, solution, app = await _shared_runtime_fixture(
        db_session,
        is_superuser=True,
    )
    captured_bypass: list[bool] = []

    async def fake_resolve_authorization_context(*args, **kwargs):
        return _authorization(user)

    class FakeApplicationRepository:
        def __init__(self, *args, bypass_resource_roles: bool, **kwargs):
            captured_bypass.append(bypass_resource_roles)

        async def can_access(self, **kwargs):
            raise AccessDeniedError("denied")

    monkeypatch.setattr(
        app_runtime_access,
        "resolve_authorization_context",
        fake_resolve_authorization_context,
    )
    monkeypatch.setattr(
        app_runtime_access,
        "ApplicationRepository",
        FakeApplicationRepository,
    )

    viewer = await load_runtime_viewer(
        db_session,
        user_id=user.id,
        solution_id=solution.id,
        app_id=app.id,
        organization_id=solution.organization_id,
    )

    assert viewer is None
    assert captured_bypass == [False]
