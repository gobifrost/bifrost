from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.routers import files
from src.services.authorization import (
    AuthorizationBoundary,
    AuthorizationContext,
    AuthorizationGrantSource,
)


def _principal() -> UserPrincipal:
    return UserPrincipal(
        user_id=uuid4(),
        email="operator@example.com",
        organization_id=uuid4(),
        name="Operator",
    )


def _authorization(*, boundary: AuthorizationBoundary, capability: str) -> AuthorizationContext:
    principal = _principal()
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary,
        effective_capabilities=frozenset({capability}),
        grant_sources=(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "kwargs", "capability"),
    [
        (
            files.pull_files,
            {
                "request": files.FilePullRequest(prefix="", local_hashes={}),
                "db": AsyncMock(),
            },
            "repository.read",
        ),
        (
            files.get_manifest,
            {
                "db": AsyncMock(),
            },
            "repository.read",
        ),
        (
            files.list_files_editor,
            {
                "path": ".",
                "recursive": False,
                "db": AsyncMock(),
            },
            "repository.read",
        ),
        (
            files.put_file_content_editor,
            {
                "request": files.FileContentRequest(
                    path="apps/example.tsx",
                    content="export {}",
                    encoding="utf-8",
                ),
                "db": AsyncMock(),
            },
            "repository.readwrite",
        ),
    ],
)
async def test_source_workspace_admin_routes_require_platform_boundary(
    handler,
    kwargs,
    capability,
) -> None:
    authorization = _authorization(
        boundary=AuthorizationBoundary.organization(uuid4()),
        capability=capability,
    )

    with pytest.raises(HTTPException) as exc_info:
        await handler(authorization=authorization, **kwargs)

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_watch_session_uses_central_requester_identity() -> None:
    principal = _principal()
    authorization = AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=AuthorizationBoundary.platform(),
        effective_capabilities=frozenset({"repository.read"}),
        grant_sources=(),
    )
    redis = MagicMock()
    redis.setex = AsyncMock()
    redis.delete = AsyncMock()

    with (
        patch(
            "src.core.cache.redis_client.get_shared_redis",
            AsyncMock(return_value=redis),
        ),
        patch("src.core.pubsub.publish_file_activity", AsyncMock()) as publish,
    ):
        await files.manage_watch_session(
            files.WatchSessionRequest(
                action="start",
                prefix="apps",
                session_id="session-1",
            ),
            authorization,
        )

    redis.setex.assert_awaited_once()
    key, ttl, payload = redis.setex.await_args.args
    assert key == f"bifrost:watch:{principal.user_id}:apps"
    assert ttl == files.WATCH_SESSION_TTL_SECONDS
    assert '"user_id": "{}"'.format(principal.user_id) in payload
    assert '"user_name": "Operator"' in payload
    publish.assert_awaited_once_with(
        user_id=str(principal.user_id),
        user_name="Operator",
        activity_type="watch_start",
        prefix="apps",
        session_id="session-1",
    )


@pytest.mark.asyncio
async def test_file_policy_test_uses_target_users_selected_boundary_roles() -> None:
    org_id = uuid4()
    target = SimpleNamespace(
        id=uuid4(),
        email="target@example.com",
        organization_id=org_id,
        name="Target",
        is_active=True,
        is_superuser=False,
        is_verified=True,
        is_external=False,
    )
    db_result = MagicMock()
    db_result.scalar_one_or_none.return_value = target
    db = MagicMock()
    db.execute = AsyncMock(return_value=db_result)
    requester = _principal()
    authorization = AuthorizationContext(
        requester=requester,
        effective_actor=requester,
        selected_boundary=AuthorizationBoundary.organization(org_id),
        effective_capabilities=frozenset({"filepolicies.read"}),
        grant_sources=(
            AuthorizationGrantSource(
                assignment_id=uuid4(),
                role_id=uuid4(),
                role_name="Support",
                capabilities=frozenset({"filepolicies.read"}),
                covering_boundary_kind="organization_group",
                covering_boundary_id=uuid4(),
            ),
        ),
    )
    selected_role_id = uuid4()
    target_authorization = AuthorizationContext(
        requester=UserPrincipal(
            user_id=target.id,
            email=target.email,
            organization_id=target.organization_id,
        ),
        effective_actor=UserPrincipal(
            user_id=target.id,
            email=target.email,
            organization_id=target.organization_id,
        ),
        selected_boundary=AuthorizationBoundary.organization(org_id),
        effective_capabilities=frozenset(),
        grant_sources=(
            AuthorizationGrantSource(
                assignment_id=uuid4(),
                role_id=selected_role_id,
                role_name="Selected Customer Role",
                capabilities=frozenset(),
                covering_boundary_kind="organization",
                covering_boundary_id=org_id,
            ),
        ),
    )

    with patch(
        "src.routers.files.resolve_authorization_context",
        AsyncMock(return_value=target_authorization),
    ):
        principal = await files._test_principal(
            SimpleNamespace(user=requester),
            db,
            str(target.id),
            authorization,
        )

    assert principal.role_ids == [selected_role_id]
    assert principal.role_names == ["Selected Customer Role"]


@pytest.mark.asyncio
async def test_file_policy_test_self_uses_selected_boundary_roles_not_stale_roles() -> None:
    org_id = uuid4()
    stale_role_id = uuid4()
    selected_role_id = uuid4()
    requester = UserPrincipal(
        user_id=uuid4(),
        email="operator@example.com",
        organization_id=org_id,
        role_ids=[stale_role_id],
        role_names=["Other Org Role"],
    )
    authorization = AuthorizationContext(
        requester=requester,
        effective_actor=requester,
        selected_boundary=AuthorizationBoundary.organization(org_id),
        effective_capabilities=frozenset({"filepolicies.read"}),
        grant_sources=(
            AuthorizationGrantSource(
                assignment_id=uuid4(),
                role_id=selected_role_id,
                role_name="Selected Customer Role",
                capabilities=frozenset({"filepolicies.read"}),
                covering_boundary_kind="organization",
                covering_boundary_id=org_id,
            ),
        ),
    )

    principal = await files._test_principal(
        SimpleNamespace(user=requester),
        MagicMock(),
        None,
        authorization,
    )

    assert principal is not requester
    assert principal.role_ids == [selected_role_id]
    assert principal.role_names == ["Selected Customer Role"]
    assert requester.role_ids == [stale_role_id]
    assert requester.role_names == ["Other Org Role"]
