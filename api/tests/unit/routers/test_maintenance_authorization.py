"""Authorization boundaries for maintenance routes."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.artifact_retention import ArtifactRetentionSettingsUpdate
from src.models.contracts.platform_jobs import PlatformJobStatus
from src.routers import maintenance
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *,
    capabilities: set[str],
    boundary: AuthorizationBoundary | None = None,
    email: str = "ops@example.com",
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=uuid4(),
        email=email,
        name="Ops User",
        organization_id=None,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary or AuthorizationBoundary.platform(),
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


def test_maintenance_helper_requires_platform_boundary() -> None:
    authorization = _authorization(
        capabilities={"repository.read"},
        boundary=AuthorizationBoundary.organization(uuid4()),
    )

    with pytest.raises(HTTPException) as exc:
        maintenance._require_platform_maintenance(authorization, "repository.read")

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        "select platform"
    )


def test_maintenance_helper_requires_capability() -> None:
    authorization = _authorization(capabilities={"repository.read"})

    with pytest.raises(HTTPException) as exc:
        maintenance._require_platform_maintenance(
            authorization,
            "repository.readwrite",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: repository.readwrite"


@pytest.mark.asyncio
async def test_update_artifact_retention_uses_effective_actor_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updated_by_values: list[str] = []
    audit_events: list[tuple[str, dict[str, object]]] = []
    commits = 0

    class _DB:
        async def commit(self) -> None:
            nonlocal commits
            commits += 1

    class _Service:
        def __init__(self, db: _DB) -> None:
            self.db = db

        async def update_settings(self, settings, *, updated_by: str):  # noqa: ANN001, ANN201
            updated_by_values.append(updated_by)
            return settings

    async def _emit_audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audit_events.append((action, kwargs))

    monkeypatch.setattr(maintenance, "ArtifactRetentionSettingsService", _Service)
    monkeypatch.setattr(maintenance, "emit_audit", _emit_audit)

    result = await maintenance.update_artifact_retention_settings(
        ArtifactRetentionSettingsUpdate(enabled=True, retention_days=30),
        _authorization(
            capabilities={"platformjobs.execute"},
            email="operator@example.com",
        ),
        _DB(),
    )

    assert result.enabled is True
    assert result.retention_days == 30
    assert updated_by_values == ["operator@example.com"]
    assert commits == 1
    assert audit_events == [
        (
            "artifact_retention.settings.update",
            {
                "resource_type": "artifact_retention_settings",
                "details": {
                    "enabled": True,
                    "retention_days": 30,
                },
            },
        )
    ]


@pytest.mark.asyncio
async def test_reimport_from_repo_uses_effective_actor_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: dict[str, object] = {}
    audit_events: list[tuple[str, dict[str, object]]] = []
    commits = 0

    class _DB:
        async def commit(self) -> None:
            nonlocal commits
            commits += 1

    async def _enqueue_platform_job(db, definition, payload, **kwargs):  # noqa: ANN001, ANN003, ANN201
        requested.update(kwargs)
        return SimpleNamespace(id=uuid4(), status=PlatformJobStatus.QUEUED), False

    async def _publish_platform_job_update(job):  # noqa: ANN001, ANN201
        requested["published_job_id"] = job.id

    async def _emit_audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audit_events.append((action, kwargs))

    monkeypatch.setattr(
        "src.services.platform_jobs.enqueue_platform_job",
        _enqueue_platform_job,
    )
    monkeypatch.setattr(
        "src.services.platform_jobs.publish_platform_job_update",
        _publish_platform_job_update,
    )
    monkeypatch.setattr(maintenance, "emit_audit", _emit_audit)

    authorization = _authorization(
        capabilities={"repository.readwrite"},
        email="operator@example.com",
    )
    result = await maintenance.reimport_from_repo(
        SimpleNamespace(db=_DB()),
        authorization,
    )

    assert result.status == PlatformJobStatus.QUEUED
    assert requested["requested_by_user_id"] == authorization.effective_actor.user_id
    assert requested["requested_by_email"] == "operator@example.com"
    assert requested["requested_by_name"] == "Ops User"
    assert requested["resource_type"] == "workspace"
    assert requested["resource_id"] == "_repo"
    assert str(requested["published_job_id"]) == result.job_id
    assert commits == 1
    assert audit_events == [
        (
            "maintenance.repository.reimport.enqueue",
            {
                "resource_type": "platform_job",
                "resource_id": requested["published_job_id"],
                "details": {"resource_id": "_repo"},
            },
        )
    ]
