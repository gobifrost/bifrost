"""Authorization boundaries for OAuth connection management routes."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.platform_jobs import PlatformJobStatus
from src.routers import oauth_connections
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
    email: str = "builder@example.com",
) -> AuthorizationContext:
    organization_id = uuid4()
    principal = UserPrincipal(
        user_id=uuid4(),
        email=email,
        name="Builder User",
        organization_id=organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary
        or AuthorizationBoundary.organization(organization_id),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


def test_provider_definition_helper_requires_integrations_readwrite_platform() -> None:
    authorization = _authorization(
        capabilities={"integrations.readwrite"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )

    with pytest.raises(HTTPException) as exc:
        oauth_connections._require_oauth_provider_definition(
            authorization,
            "integrations.readwrite",
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        "select platform"
    )


def test_token_scope_helper_requires_exact_organization_boundary() -> None:
    target_org_id = uuid4()
    authorization = _authorization(
        capabilities={"integrations.read"},
        boundary=AuthorizationBoundary.organization(uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        oauth_connections._require_oauth_token_scope(
            authorization,
            "integrations.read",
            target_org_id,
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        f"select organization:{target_org_id}"
    )


@pytest.mark.asyncio
async def test_get_credentials_uses_selected_org_for_global_provider_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected_org_id = uuid4()
    repo_org_ids: list[object] = []
    now = datetime.now(timezone.utc)
    provider = SimpleNamespace(
        id=uuid4(),
        organization_id=None,
        status="not_connected",
        integration_id=uuid4(),
        provider_name="example",
    )

    class _Repo:
        def __init__(self, db, *, org_id, bypass_resource_admission):  # noqa: ANN001
            repo_org_ids.append(org_id)

        async def get_by_connection_name(self, connection_name: str):  # noqa: ANN201
            assert connection_name == "example"
            return provider

        async def get_token(self, connection_name: str):  # noqa: ANN201
            assert connection_name == "example"
            return None

    monkeypatch.setattr(oauth_connections, "OAuthProviderRepository", _Repo)

    result = await oauth_connections.get_credentials(
        "example",
        SimpleNamespace(db=object()),
        _authorization(
            capabilities={"integrations.read"},
            boundary=AuthorizationBoundary.organization(selected_org_id),
        ),
    )

    assert repo_org_ids == [selected_org_id, selected_org_id]
    assert result.connection_name == "example"
    assert result.credentials is None
    assert result.status == "not_connected"
    assert result.expires_at is None
    assert now.tzinfo is not None


@pytest.mark.asyncio
async def test_trigger_refresh_all_requires_platform_and_uses_effective_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: dict[str, object] = {}
    audit_actions: list[str] = []

    class _DB:
        async def commit(self) -> None:
            requested["committed"] = True

    async def _enqueue_platform_job(db, definition, payload, **kwargs):  # noqa: ANN001, ANN003, ANN201
        requested.update(kwargs)
        return (
            SimpleNamespace(id=uuid4(), status=PlatformJobStatus.QUEUED),
            False,
        )

    async def _publish_platform_job_update(job):  # noqa: ANN001, ANN201
        requested["published_job_id"] = job.id

    async def _emit_audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audit_actions.append(action)
        requested["audit_resource_id"] = kwargs["resource_id"]

    monkeypatch.setattr(
        oauth_connections, "enqueue_platform_job", _enqueue_platform_job
    )
    monkeypatch.setattr(
        oauth_connections,
        "publish_platform_job_update",
        _publish_platform_job_update,
    )
    monkeypatch.setattr(oauth_connections, "emit_audit", _emit_audit)

    authorization = _authorization(
        capabilities={"integrations.readwrite"},
        boundary=AuthorizationBoundary.platform(),
        email="ops@example.com",
    )

    result = await oauth_connections.trigger_refresh_all(
        SimpleNamespace(db=_DB()),
        authorization,
    )

    assert result.status == PlatformJobStatus.QUEUED
    assert requested["requested_by_user_id"] == authorization.effective_actor.user_id
    assert requested["requested_by_email"] == "ops@example.com"
    assert requested["requested_by_name"] == "Builder User"
    assert requested["organization_id"] is None
    assert requested["committed"] is True
    assert requested["published_job_id"] == result.job_id
    assert requested["audit_resource_id"] == result.job_id
    assert audit_actions == ["oauth_connection.refresh_all"]


@pytest.mark.asyncio
async def test_trigger_refresh_all_rejects_organization_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _enqueue_platform_job(*args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        pytest.fail("refresh job should not enqueue without platform boundary")

    monkeypatch.setattr(
        oauth_connections, "enqueue_platform_job", _enqueue_platform_job
    )

    with pytest.raises(HTTPException) as exc:
        await oauth_connections.trigger_refresh_all(
            SimpleNamespace(db=object()),
            _authorization(
                capabilities={"integrations.readwrite"},
                boundary=AuthorizationBoundary.organization(uuid4()),
            ),
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        "select platform"
    )
