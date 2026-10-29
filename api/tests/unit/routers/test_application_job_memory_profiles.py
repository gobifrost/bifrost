"""App deploys and SDK updates learn their memory per App.

Both are isolated (build-class) job types: an attempt Kubernetes stops at
its pod's memory limit teaches the next one a larger pod, through the
memory profile key they are enqueued with.
"""

from __future__ import annotations

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Response, UploadFile

from src.routers import applications


def _application() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        solution_id=None,
        app_model="standalone_v2",
        organization_id=None,
        name="Contoso Portal",
        slug="contoso-portal",
        active_deployment_id=uuid4(),
        sdk_package_version=None,
        sdk_fingerprint=None,
        sdk_contract_version=None,
        sdk_built_at=None,
    )


def _enqueue(monkeypatch: pytest.MonkeyPatch, user_id) -> AsyncMock:  # type: ignore[no-untyped-def]
    job = SimpleNamespace(
        id=uuid4(), status="queued", notification_id=uuid4(), requested_by_user_id=str(user_id)
    )
    enqueue = AsyncMock(return_value=(job, False))
    monkeypatch.setattr(applications, "enqueue_platform_job", enqueue)
    monkeypatch.setattr(applications, "publish_platform_job_update", AsyncMock())
    return enqueue


@pytest.mark.asyncio
async def test_an_app_deploy_is_enqueued_with_its_apps_memory_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    user = SimpleNamespace(user_id=uuid4(), email="admin@contoso.example", name="Admin")
    enqueue = _enqueue(monkeypatch, user.user_id)
    monkeypatch.setattr(
        applications, "get_application_for_write_or_404", AsyncMock(return_value=application)
    )
    storage = SimpleNamespace(write_path=AsyncMock(return_value=("a" * 64, 3)), delete=AsyncMock())
    monkeypatch.setattr(applications, "ApplicationDeployStorage", lambda job_id: storage)
    db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())

    await applications.deploy_application(
        application.id,
        UploadFile(file=io.BytesIO(b"zip"), filename="source.zip"),
        ctx=SimpleNamespace(db=db),  # type: ignore[arg-type]
        user=user,  # type: ignore[arg-type]
        response=Response(),
    )

    assert enqueue.await_args.kwargs["memory_profile_key"] == (
        f"application.deploy.memory.v1:{application.id}"
    )


@pytest.mark.asyncio
async def test_an_sdk_update_is_enqueued_with_its_apps_memory_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = _application()
    user = SimpleNamespace(user_id=uuid4(), email="admin@contoso.example", name="Admin")
    enqueue = _enqueue(monkeypatch, user.user_id)

    await applications._enqueue_sdk_update_for_application(
        ctx=SimpleNamespace(db=SimpleNamespace()),  # type: ignore[arg-type]
        user=user,  # type: ignore[arg-type]
        application=application,  # type: ignore[arg-type]
    )

    assert enqueue.await_args.kwargs["memory_profile_key"] == (
        f"application.sdk_update.memory.v1:{application.id}"
    )
