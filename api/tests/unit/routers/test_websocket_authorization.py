from __future__ import annotations

import uuid

import pytest

from src.core.auth import UserPrincipal
from src.routers import websocket as ws_mod
from src.services.authorization import (
    AuthorizationBoundary,
    AuthorizationBoundaryKind,
    AuthorizationContext,
    AuthorizationGrantSource,
)


def _user(org_id: uuid.UUID | None = None) -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid.uuid4(),
        email="user@example.com",
        organization_id=org_id,
        is_superuser=False,
    )


def _authorization(
    *,
    capabilities: set[str],
    organization_id: uuid.UUID | None = None,
    boundary: AuthorizationBoundary | None = None,
) -> AuthorizationContext:
    principal = _user(organization_id)
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=(
            boundary
            or (
                AuthorizationBoundary.organization(organization_id)
                if organization_id is not None
                else AuthorizationBoundary.platform()
            )
        ),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


def _authorization_with_role(
    user,
    *,
    selected_org_id,
    role_id,
    role_name="Selected Org Role",
) -> AuthorizationContext:
    return AuthorizationContext(
        requester=user,
        effective_actor=user,
        selected_boundary=AuthorizationBoundary.organization(selected_org_id),
        effective_capabilities=frozenset({"tabledocuments.read"}),
        grant_sources=(
            AuthorizationGrantSource(
                assignment_id=uuid.uuid4(),
                role_id=role_id,
                role_name=role_name,
                capabilities=frozenset({"tabledocuments.read"}),
                covering_boundary_kind="organization",
                covering_boundary_id=selected_org_id,
            ),
        ),
    )


def test_platform_channel_requires_exact_platform_boundary() -> None:
    org_id = uuid.uuid4()
    platform = _authorization(capabilities={"repository.read"})
    org = _authorization(capabilities={"repository.read"}, organization_id=org_id)

    assert ws_mod._has_platform_capability(platform, "repository.read")
    assert not ws_mod._has_platform_capability(org, "repository.read")


def test_platform_channel_requires_capability() -> None:
    authorization = _authorization(capabilities={"metrics.read"})

    assert not ws_mod._has_platform_capability(
        authorization,
        "platformjobs.read",
    )


def test_file_scope_requires_exact_managed_file_boundary() -> None:
    org_id = uuid.uuid4()
    other_org_id = uuid.uuid4()
    authorization = _authorization(
        capabilities={"managedfiles.read"},
        organization_id=org_id,
    )

    assert ws_mod._file_org_and_scope(
        user=_user(org_id),
        authorization=authorization,
        location="managed",
        requested_scope=str(org_id),
    ) == (org_id, str(org_id))
    assert ws_mod._file_org_and_scope(
        user=_user(org_id),
        authorization=authorization,
        location="managed",
        requested_scope=str(other_org_id),
    ) is None


def test_file_global_scope_requires_platform_boundary() -> None:
    org_id = uuid.uuid4()
    platform = _authorization(capabilities={"managedfiles.read"})
    org = _authorization(capabilities={"managedfiles.read"}, organization_id=org_id)

    assert ws_mod._file_org_and_scope(
        user=_user(org_id),
        authorization=platform,
        location="managed",
        requested_scope="global",
    ) == (None, "global")
    assert ws_mod._file_org_and_scope(
        user=_user(org_id),
        authorization=org,
        location="managed",
        requested_scope="global",
    ) is None


@pytest.mark.asyncio
async def test_websocket_org_resource_requires_exact_selected_org() -> None:
    org_id = uuid.uuid4()
    other_org_id = uuid.uuid4()
    authorization = _authorization(
        capabilities={"executions.read"},
        organization_id=org_id,
    )

    assert await ws_mod._authorization_can_access_org_resource(
        authorization,
        "executions.read",
        org_id,
    )
    assert not await ws_mod._authorization_can_access_org_resource(
        authorization,
        "executions.read",
        other_org_id,
    )


@pytest.mark.asyncio
async def test_websocket_platform_boundary_is_global_not_org_wildcard() -> None:
    org_id = uuid.uuid4()
    authorization = _authorization(capabilities={"apps.read"})

    assert await ws_mod._authorization_can_access_org_resource(
        authorization,
        "apps.read",
        None,
    )
    assert not await ws_mod._authorization_can_access_org_resource(
        authorization,
        "apps.read",
        org_id,
    )


@pytest.mark.asyncio
async def test_websocket_exact_org_read_can_cascade_to_global_app_or_table() -> None:
    org_id = uuid.uuid4()
    authorization = _authorization(
        capabilities={"apps.read", "tabledocuments.read"},
        organization_id=org_id,
    )

    assert await ws_mod._authorization_can_access_org_resource(
        authorization,
        "apps.read",
        None,
        allow_global_cascade=True,
    )
    assert await ws_mod._authorization_can_access_org_resource(
        authorization,
        "tabledocuments.read",
        None,
        allow_global_cascade=True,
    )


@pytest.mark.asyncio
async def test_websocket_exact_org_execution_does_not_cascade_to_global() -> None:
    org_id = uuid.uuid4()
    authorization = _authorization(
        capabilities={"executions.read"},
        organization_id=org_id,
    )

    assert not await ws_mod._authorization_can_access_org_resource(
        authorization,
        "executions.read",
        None,
    )


@pytest.mark.asyncio
async def test_websocket_platform_superuser_is_wildcard() -> None:
    org_id = uuid.uuid4()
    authorization = _authorization(capabilities={"platform.superuser"})

    assert await ws_mod._authorization_can_access_org_resource(
        authorization,
        "apps.read",
        org_id,
    )


@pytest.mark.asyncio
async def test_websocket_managed_boundary_covers_customer_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    customer_org_id = uuid.uuid4()
    provider_org_id = uuid.uuid4()

    class _FakeDb:
        async def scalar(self, _stmt):  # type: ignore[no-untyped-def]
            return self.is_provider

    class _FakeContext:
        def __init__(self, is_provider: bool) -> None:
            self.db = _FakeDb()
            self.db.is_provider = is_provider

        async def __aenter__(self) -> _FakeDb:
            return self.db

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    authorization = _authorization(
        capabilities={"apps.read"},
        boundary=AuthorizationBoundary(AuthorizationBoundaryKind.MANAGED_ORGANIZATIONS),
    )

    monkeypatch.setattr(ws_mod, "get_db_context", lambda: _FakeContext(False))
    assert await ws_mod._authorization_can_access_org_resource(
        authorization,
        "apps.read",
        customer_org_id,
    )

    monkeypatch.setattr(ws_mod, "get_db_context", lambda: _FakeContext(True))
    assert not await ws_mod._authorization_can_access_org_resource(
        authorization,
        "apps.read",
        provider_org_id,
    )


@pytest.mark.asyncio
async def test_websocket_form_embed_execution_access_stays_denied_without_hmac() -> None:
    user = _user(uuid.uuid4())
    user.embed = True
    user.embed_kind = "form"
    user.grant = None

    assert not await ws_mod.can_access_execution(
        user,
        str(uuid.uuid4()),
        _authorization(capabilities={"platform.superuser"}),
    )


@pytest.mark.asyncio
async def test_websocket_execution_without_authorization_has_no_admin_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user(uuid.uuid4())
    user.is_superuser = True
    execution_id = uuid.uuid4()
    other_org_id = uuid.uuid4()

    class _Result:
        def one_or_none(self):  # type: ignore[no-untyped-def]
            return (uuid.uuid4(), other_org_id)

    class _FakeDb:
        async def execute(self, _stmt):  # type: ignore[no-untyped-def]
            return _Result()

    class _FakeContext:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return _FakeDb()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    monkeypatch.setattr(ws_mod, "get_db_context", lambda: _FakeContext())

    assert not await ws_mod.can_access_execution(user, str(execution_id), None)


@pytest.mark.asyncio
async def test_websocket_app_without_authorization_has_no_admin_wildcard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user(uuid.uuid4())
    user.is_superuser = True
    app_id = uuid.uuid4()
    other_org_id = uuid.uuid4()

    class _Result:
        def one_or_none(self):  # type: ignore[no-untyped-def]
            return (other_org_id,)

    class _FakeDb:
        async def execute(self, _stmt):  # type: ignore[no-untyped-def]
            return _Result()

    class _FakeContext:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return _FakeDb()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    monkeypatch.setattr(ws_mod, "get_db_context", lambda: _FakeContext())

    assert not await ws_mod.can_access_app(user, str(app_id), None)


@pytest.mark.asyncio
async def test_websocket_policy_principal_uses_selected_boundary_roles() -> None:
    user = _user(uuid.uuid4())
    stale_other_org_role_id = uuid.uuid4()
    selected_role_id = uuid.uuid4()
    user.role_ids = [stale_other_org_role_id]
    user.role_names = ["Other Org Role"]
    authorization = _authorization_with_role(
        user,
        selected_org_id=user.organization_id,
        role_id=selected_role_id,
    )

    policy_user = await ws_mod._policy_principal(user, authorization)

    assert policy_user is not user
    assert policy_user.role_ids == [selected_role_id]
    assert policy_user.role_names == ["Selected Org Role"]
    assert user.role_ids == [stale_other_org_role_id]


@pytest.mark.asyncio
async def test_websocket_policy_principal_preserves_runtime_role_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = _user(uuid.uuid4())
    runtime_role_id = uuid.uuid4()

    class _FakeContext:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):  # type: ignore[no-untyped-def]
            return None

    async def _roles(_user_id, _db):  # type: ignore[no-untyped-def]
        return [runtime_role_id], ["Runtime Role"]

    monkeypatch.setattr(ws_mod, "get_db_context", lambda: _FakeContext())
    monkeypatch.setattr(ws_mod, "get_user_roles", _roles)

    policy_user = await ws_mod._policy_principal(user, None)

    assert policy_user is not user
    assert policy_user.role_ids == [runtime_role_id]
    assert policy_user.role_names == ["Runtime Role"]
