"""Inventory adoption fences service and platform-job leases in PostgreSQL."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from src.models.orm.platform_jobs import PlatformJob
from src.models.orm.services import ServiceAttempt, ServiceDefinition
from src.models.orm.workflows import Workflow
from src.worker.remote.remote_pool import ThinRemoteProcessPool


def _service_route(
    attempt: ServiceAttempt,
    definition: ServiceDefinition,
    *,
    lease_token: str | None = None,
) -> dict[str, object]:
    return {
        "route_id": f"service:{attempt.id}",
        "kind": "service",
        "subject_id": str(attempt.id),
        "memory_request_bytes": 192 * 2**20,
        "service": {
            "service_id": str(definition.id),
            "attempt_id": str(attempt.id),
            "lease_token": lease_token or attempt.lease_token,
        },
    }


def _platform_route(job: PlatformJob, *, lease_token: str | None = None) -> dict[str, object]:
    return {
        "route_id": f"platform:{job.id}",
        "kind": "platform_job",
        "subject_id": str(job.id),
        "lease_token": lease_token or str(job.lease_token),
        "memory_request_bytes": job.memory_required_bytes,
    }


async def _service_attempt(
    async_session_factory, *, worker_id: str
) -> tuple[ServiceDefinition, ServiceAttempt]:
    suffix = uuid4().hex
    workflow = Workflow(
        name=f"inventory-service-{suffix}",
        function_name=f"inventory_service_{suffix}",
        path=f"workflows/inventory_service_{suffix}.py",
        type="service",
    )
    async with async_session_factory() as session:
        session.add(workflow)
        await session.flush()
        definition = ServiceDefinition(workflow_id=workflow.id, created_by="inventory-test")
        session.add(definition)
        await session.flush()
        attempt = ServiceAttempt(
            service_id=definition.id,
            worker_id=worker_id,
            lease_token=f"lease-{suffix}",
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            state="running",
        )
        session.add(attempt)
        await session.commit()
        return definition, attempt


async def _platform_job(async_session_factory) -> PlatformJob:
    async with async_session_factory() as session:
        job = PlatformJob(
            job_type="workspace.git",
            payload={},
            requested_by_user_id=str(uuid4()),
            requested_by_email="inventory@example.test",
            requested_by_name="Inventory Test",
            title="Inventory platform job",
            status="running",
            lease_token=uuid4(),
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            memory_required_bytes=320 * 2**20,
        )
        session.add(job)
        await session.commit()
        return job


async def _cleanup_service_claim(
    async_session_factory,
    *,
    workflow_id: UUID,
    definition_id: UUID,
    attempt_id: UUID,
) -> None:
    """Remove precisely the service-claim rows seeded by one test."""
    async with async_session_factory() as session:
        await session.execute(delete(ServiceAttempt).where(ServiceAttempt.id == attempt_id))
        await session.execute(
            delete(ServiceDefinition).where(ServiceDefinition.id == definition_id)
        )
        await session.execute(delete(Workflow).where(Workflow.id == workflow_id))
        await session.commit()


async def _cleanup_platform_job(async_session_factory, job_id: UUID) -> None:
    """Remove precisely the platform-job row seeded by one test."""
    async with async_session_factory() as session:
        await session.execute(delete(PlatformJob).where(PlatformJob.id == job_id))
        await session.commit()


@pytest.mark.asyncio
async def test_inventory_adopts_service_claim_and_restores_its_memory_reservation(
    async_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session

    monkeypatch.setattr("src.core.database.get_db_context", context)
    worker_id = str(uuid4())
    definition, attempt = await _service_attempt(async_session_factory, worker_id=worker_id)
    try:
        transport = AsyncMock()
        pool = ThinRemoteProcessPool(transport, worker_id=worker_id)

        snapshot = await pool.reconcile_inventory(
            {"version": 1, "routes": [_service_route(attempt, definition)], "terminal_routes": []}
        )

        assert len(snapshot["services"]) == 1
        owned = snapshot["services"][0]
        assert owned.attempt_id == attempt.id
        assert owned.lease_token == attempt.lease_token
        assert owned.memory_request_bytes == 192 * 2**20
        assert pool.service_processes
        transport.request.assert_not_awaited()
    finally:
        await _cleanup_service_claim(
            async_session_factory,
            workflow_id=definition.workflow_id,
            definition_id=definition.id,
            attempt_id=attempt.id,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_claim", ["worker", "token", "expired"])
async def test_inventory_rejects_service_with_wrong_worker_token_or_expired_lease(
    async_session_factory,
    monkeypatch: pytest.MonkeyPatch,
    invalid_claim: str,
) -> None:
    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session

    monkeypatch.setattr("src.core.database.get_db_context", context)
    worker_id = str(uuid4())
    definition, attempt = await _service_attempt(async_session_factory, worker_id=worker_id)
    try:
        route = _service_route(attempt, definition)
        if invalid_claim == "worker":
            async with async_session_factory() as session:
                row = await session.get(ServiceAttempt, attempt.id)
                assert row is not None
                row.worker_id = str(uuid4())
                await session.commit()
        elif invalid_claim == "token":
            route = _service_route(attempt, definition, lease_token="wrong-lease")
        else:
            async with async_session_factory() as session:
                row = await session.get(ServiceAttempt, attempt.id)
                assert row is not None
                row.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                await session.commit()

        transport = AsyncMock()
        pool = ThinRemoteProcessPool(transport, worker_id=worker_id)
        snapshot = await pool.reconcile_inventory(
            {"version": 1, "routes": [route], "terminal_routes": []}
        )

        assert snapshot["services"] == []
        assert pool.service_processes == {}
        transport.request.assert_awaited_once_with(
            "terminate",
            {
                "route_id": route["route_id"],
                "error_type": "OwnerLost",
                "error": "Claim no longer belongs to this launcher",
            },
        )
    finally:
        await _cleanup_service_claim(
            async_session_factory,
            workflow_id=definition.workflow_id,
            definition_id=definition.id,
            attempt_id=attempt.id,
        )


@pytest.mark.asyncio
async def test_inventory_adopts_platform_job_with_the_original_lease_and_reservation(
    async_session_factory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session

    monkeypatch.setattr("src.core.database.get_db_context", context)
    job = await _platform_job(async_session_factory)
    try:
        transport = AsyncMock()
        pool = ThinRemoteProcessPool(transport, worker_id=str(uuid4()))
        route = _platform_route(job)

        snapshot = await pool.reconcile_inventory(
            {"version": 1, "routes": [route], "terminal_routes": []}
        )

        assert len(snapshot["platform_jobs"]) == 1
        route_id, claim = snapshot["platform_jobs"][0]
        assert route_id == route["route_id"]
        assert claim.id == job.id
        assert claim.lease_token == job.lease_token
        assert claim.memory_required_bytes == job.memory_required_bytes
        transport.request.assert_not_awaited()
    finally:
        await _cleanup_platform_job(async_session_factory, job.id)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_claim", ["token", "terminal"])
async def test_inventory_rejects_platform_job_with_wrong_token_or_terminal_status(
    async_session_factory,
    monkeypatch: pytest.MonkeyPatch,
    invalid_claim: str,
) -> None:
    @asynccontextmanager
    async def context():
        async with async_session_factory() as session:
            yield session

    monkeypatch.setattr("src.core.database.get_db_context", context)
    job = await _platform_job(async_session_factory)
    try:
        route = _platform_route(job)
        if invalid_claim == "token":
            route = _platform_route(job, lease_token=str(uuid4()))
        else:
            async with async_session_factory() as session:
                row = await session.get(PlatformJob, job.id)
                assert row is not None
                row.status = "succeeded"
                await session.commit()

        transport = AsyncMock()
        pool = ThinRemoteProcessPool(transport, worker_id=str(uuid4()))
        snapshot = await pool.reconcile_inventory(
            {"version": 1, "routes": [], "terminal_routes": [route]}
        )

        assert snapshot["platform_jobs"] == []
        transport.request.assert_awaited_once_with(
            "terminate_job",
            {
                "route_id": route["route_id"],
                "error_type": "OwnerLost",
                "error": "Claim no longer belongs to this launcher",
            },
        )
    finally:
        await _cleanup_platform_job(async_session_factory, job.id)
