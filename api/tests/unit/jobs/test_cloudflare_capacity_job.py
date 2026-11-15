"""API-owned Cloudflare capacity apply job contracts."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from shared.models import CloudflareCapacityApplyPayload, WorkerCapacityProfile
from src.jobs.platform.base import (
    PlatformJobCancelled,
    PlatformJobFailure,
    PlatformJobRequiresAction,
)
from src.services.worker_pool.cloudflare_deploy import (
    CloudflareDeploymentResult,
)
from src.services.worker_pool.cloudflare_image import CopiedWorkerImage
from src.services.worker_pool.connection import CloudflareConnection


API_KEY = "cloudflare-api-key-that-must-never-be-public"
CONTROL_TOKEN = "cloudflare-control-token-that-must-never-be-public"
ACCOUNT_A = "a" * 32
ACCOUNT_B = "b" * 32


class _Context:
    checkpoint = None

    def __init__(self) -> None:
        self.reports: list[tuple[str, int | None]] = []

    async def authorize_effect(self, _db) -> bool:
        return True

    async def report(self, phase: str, *, percent: int | None = None) -> None:
        self.reports.append((phase, percent))

    async def save_checkpoint(self, *_args, **_kwargs) -> None:
        return None


class _Http:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args) -> None:
        return None


def _connection(*, generation=None, requested_account_id: str | None = None) -> CloudflareConnection:
    return CloudflareConnection(
        generation=generation or uuid4(), scope="bifrost:capacity",
        metadata={
            "requested_account_id": requested_account_id,
            "desired_bifrost_api_origin": "https://bifrost.example.test",
            "desired_max_workers": 2,
        },
        api_key=API_KEY, capacity_token=CONTROL_TOKEN,
    )


def test_apply_definition_is_api_owned_and_never_claimable_by_physical_executors() -> None:
    from src.jobs.platform.catalog import (
        api_platform_job_types,
        executor_platform_job_types,
        get_platform_job_metadata,
    )
    from src.jobs.platform.registry import get_platform_job_definition

    metadata = get_platform_job_metadata("cloudflare.capacity.apply")
    definition = get_platform_job_definition("cloudflare.capacity.apply")

    assert metadata is not None and definition is not None
    assert metadata.execution_owner == "api"
    assert metadata.policy.timeout_seconds == 60 * 60
    assert metadata.policy.min_memory_headroom_mb == 128
    assert definition.payload_model is CloudflareCapacityApplyPayload
    assert "cloudflare.capacity.apply" in api_platform_job_types()
    assert "cloudflare.capacity.apply" not in executor_platform_job_types()


@pytest.mark.asyncio
async def test_apply_requires_account_selection_with_only_safe_account_identity_after_connection_read_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.jobs.platform import cloudflare_capacity as job

    connection = _connection()
    reader_closed = False
    state_changes: list[dict[str, object]] = []
    contexts = 0

    @asynccontextmanager
    async def db_context():
        nonlocal reader_closed, contexts
        contexts += 1
        db = SimpleNamespace(commit=AsyncMock())
        try:
            yield db
        finally:
            if contexts == 1:
                reader_closed = True

    class Adapter:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def list_accounts(self, _token):
            assert reader_closed, "account discovery must not hold the connection read session"
            return [{"id": ACCOUNT_A, "name": "First"}, {"id": ACCOUNT_B, "name": "Second"}]

    update = AsyncMock(side_effect=lambda _db, _generation, changes: state_changes.append(changes) or True)
    monkeypatch.setattr(job, "get_db_context", lambda: db_context())
    monkeypatch.setattr(job, "load_cloudflare_connection", AsyncMock(return_value=connection))
    monkeypatch.setattr(job, "update_cloudflare_connection_metadata", update)
    monkeypatch.setattr(job, "CloudflareDeploymentAdapter", Adapter)
    monkeypatch.setattr(job.httpx, "AsyncClient", _Http)
    image_copier = AsyncMock(side_effect=AssertionError("account choice must precede image copying"))
    monkeypatch.setattr(job, "CloudflareImageCopier", image_copier)

    with pytest.raises(PlatformJobRequiresAction) as raised:
        await job.run_cloudflare_capacity_apply(
            _Context(), CloudflareCapacityApplyPayload(generation=connection.generation),
        )

    assert raised.value.phase == "Choose a Cloudflare account"
    assert raised.value.result == {
        "accounts": [{"id": ACCOUNT_A, "name": "First"}, {"id": ACCOUNT_B, "name": "Second"}],
    }
    assert API_KEY not in repr(raised.value.result)
    assert CONTROL_TOKEN not in repr(raised.value.result)
    assert state_changes == [{
        "status": "account_selection_required",
        "accounts": [{"id": ACCOUNT_A, "name": "First"}, {"id": ACCOUNT_B, "name": "Second"}],
    }]
    image_copier.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_cancels_before_any_provider_work_when_generation_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.jobs.platform import cloudflare_capacity as job

    connection = _connection()

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace()

    adapter = AsyncMock()
    monkeypatch.setattr(job, "get_db_context", lambda: db_context())
    monkeypatch.setattr(job, "load_cloudflare_connection", AsyncMock(return_value=connection))
    monkeypatch.setattr(job, "CloudflareDeploymentAdapter", adapter)

    with pytest.raises(PlatformJobCancelled):
        await job.run_cloudflare_capacity_apply(_Context(), CloudflareCapacityApplyPayload(generation=uuid4()))
    adapter.assert_not_called()


@pytest.mark.asyncio
async def test_apply_rejects_profile_mismatch_without_activating_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.jobs.platform import cloudflare_capacity as job

    connection = _connection(requested_account_id=ACCOUNT_A)
    context = _Context()
    deployments = []

    @asynccontextmanager
    async def db_context():
        yield SimpleNamespace(commit=AsyncMock())

    class Adapter:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def list_accounts(self, _token):
            return [{"id": ACCOUNT_A, "name": "First"}]

        async def reconcile(self, deployment, **_kwargs):
            deployments.append(deployment)
            return CloudflareDeploymentResult(
                application_id="app", container_namespace_id="namespace",
                migration_tag="v1", worker_url="https://capacity.example.test",
            )

    class Client:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def profile(self) -> WorkerCapacityProfile:
            return WorkerCapacityProfile(
                protocol_version=2, scope=connection.scope, worker_version="test-version",
                memory_limit_bytes=job.MEMORY_LIMIT_BYTES, max_workers=1,
                max_lifetime_seconds=900, execution_handlers=list(job.EXECUTION_HANDLERS),
                compute_capabilities=list(job.COMPUTE_CAPABILITIES),
            )

    copier = SimpleNamespace(copy=lambda **_kwargs: CopiedWorkerImage(
        reference="registry.example.test/bifrost@sha256:" + "a" * 64,
        digest="sha256:" + "a" * 64, source_digest="sha256:" + "b" * 64,
        worker_version="test-version",
    ))
    activate = AsyncMock(return_value=True)
    monkeypatch.setattr(job, "get_db_context", lambda: db_context())
    monkeypatch.setattr(job, "get_settings", lambda: SimpleNamespace(
        worker_capacity_image=None, pool_max_lifetime_seconds=900,
    ))
    monkeypatch.setattr(job, "get_version", lambda: "test-version")
    monkeypatch.setattr(job, "load_cloudflare_connection", AsyncMock(return_value=connection))
    monkeypatch.setattr(job, "update_cloudflare_connection_metadata", AsyncMock(return_value=True))
    monkeypatch.setattr(job, "activate_cloudflare_connection", activate)
    monkeypatch.setattr(job, "CloudflareDeploymentAdapter", Adapter)
    monkeypatch.setattr(job, "CloudflareImageCopier", lambda: copier)
    monkeypatch.setattr(job, "ExternalCapacityClient", Client)
    monkeypatch.setattr(job.httpx, "AsyncClient", _Http)

    with pytest.raises(PlatformJobFailure, match="does not match"):
        await job.run_cloudflare_capacity_apply(
            context, CloudflareCapacityApplyPayload(generation=connection.generation),
        )

    assert len(deployments) == 1
    assert deployments[0].limits.memory_limit_bytes == 4 * 1024**3
    assert deployments[0].limits.max_workers == 2
    assert deployments[0].limits.execution_handlers == job.EXECUTION_HANDLERS
    assert deployments[0].limits.compute_capabilities == job.COMPUTE_CAPABILITIES
    activate.assert_not_awaited()
