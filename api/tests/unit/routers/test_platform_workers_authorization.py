"""Authorization boundaries for platform worker diagnostics and controls."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.models.contracts.platform import RecycleProcessRequest
from src.routers.platform import workers
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


def test_worker_helper_requires_platform_boundary() -> None:
    authorization = _authorization(
        capabilities={"platformjobs.read"},
        boundary=AuthorizationBoundary.managed_organizations(),
    )

    with pytest.raises(HTTPException) as exc:
        workers._require_platform_workers(authorization, "platformjobs.read")

    assert exc.value.status_code == 409
    assert exc.value.detail == (
        "The selected authorization boundary does not match this resource; "
        "select platform"
    )


def test_worker_helper_requires_capability() -> None:
    authorization = _authorization(capabilities=set())

    with pytest.raises(HTTPException) as exc:
        workers._require_platform_workers(authorization, "platformjobs.execute")

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing required capability: platformjobs.execute"


@pytest.mark.asyncio
async def test_recycle_process_uses_effective_actor_and_audits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker_id = "worker-1"
    published: list[dict[str, object]] = []
    audits: list[tuple[str, dict[str, object]]] = []

    class _Redis:
        async def exists(self, key: str) -> bool:
            assert key == f"bifrost:pool:{worker_id}"
            return True

        async def publish(self, channel: str, payload: str) -> None:
            assert channel == f"bifrost:pool:{worker_id}:commands"
            published.append(json.loads(payload))

    async def _get_redis() -> _Redis:
        return _Redis()

    async def _emit_audit(db, action, **kwargs):  # noqa: ANN001, ANN003, ANN201
        audits.append((action, kwargs))

    monkeypatch.setattr(workers, "_get_redis", _get_redis)
    monkeypatch.setattr(workers, "emit_audit", _emit_audit)

    authorization = _authorization(capabilities={"platformjobs.execute"})
    result = await workers.recycle_process(
        worker_id,
        12345,
        authorization,
        RecycleProcessRequest(reason="test recycle"),
        SimpleNamespace(),
    )

    assert result.success is True
    assert published == [
        {
            "action": "recycle_process",
            "pid": 12345,
            "reason": "test recycle",
            "requested_by": str(authorization.effective_actor.user_id),
            "requested_at": published[0]["requested_at"],
        }
    ]
    assert audits == [
        (
            "platform_worker.process.recycle",
            {
                "resource_type": "platform_worker",
                "details": {
                    "worker_id": worker_id,
                    "pid": 12345,
                    "reason": "test recycle",
                },
            },
        )
    ]
