"""Boundary-aware visibility for durable Platform Jobs."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.core.principal import UserPrincipal
from src.routers import platform_jobs
from src.services.authorization import AuthorizationBoundary, AuthorizationContext


def _authorization(
    *, user_id, capabilities: set[str], boundary: AuthorizationBoundary
) -> AuthorizationContext:
    principal = UserPrincipal(
        user_id=user_id,
        email="operator@example.com",
        organization_id=boundary.organization_id,
    )
    return AuthorizationContext(
        requester=principal,
        effective_actor=principal,
        selected_boundary=boundary,
        effective_capabilities=frozenset(capabilities),
        grant_sources=(),
    )


class _Db:
    def __init__(self, job) -> None:  # noqa: ANN001
        self.job = job

    async def get(self, model, job_id):  # noqa: ANN001, ANN201
        return self.job if self.job.id == job_id else None

    async def scalar(self, statement):  # noqa: ANN001, ANN201
        return False


@pytest.mark.asyncio
async def test_requester_can_read_own_job_without_admin_capability() -> None:
    user_id = uuid4()
    job = SimpleNamespace(
        id=uuid4(),
        requested_by_user_id=str(user_id),
        organization_id=uuid4(),
    )
    ctx = SimpleNamespace(db=_Db(job))

    visible = await platform_jobs._get_visible_job(
        ctx,
        _authorization(
            user_id=user_id,
            capabilities=set(),
            boundary=AuthorizationBoundary.platform(),
        ),
        job.id,
    )

    assert visible is job


@pytest.mark.asyncio
async def test_platform_job_role_access_honors_exact_boundary() -> None:
    organization_id = uuid4()
    job = SimpleNamespace(
        id=uuid4(),
        requested_by_user_id=str(uuid4()),
        organization_id=organization_id,
    )
    ctx = SimpleNamespace(db=_Db(job))

    visible = await platform_jobs._get_visible_job(
        ctx,
        _authorization(
            user_id=uuid4(),
            capabilities={"platformjobs.read"},
            boundary=AuthorizationBoundary.organization(organization_id),
        ),
        job.id,
    )
    assert visible is job

    with pytest.raises(HTTPException) as exc:
        await platform_jobs._get_visible_job(
            ctx,
            _authorization(
                user_id=uuid4(),
                capabilities={"platformjobs.read"},
                boundary=AuthorizationBoundary.organization(uuid4()),
            ),
            job.id,
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_other_users_job_requires_execute_to_cancel() -> None:
    organization_id = uuid4()
    job = SimpleNamespace(
        id=uuid4(),
        requested_by_user_id=str(uuid4()),
        organization_id=organization_id,
    )
    ctx = SimpleNamespace(db=_Db(job))

    with pytest.raises(HTTPException) as exc:
        await platform_jobs._get_visible_job(
            ctx,
            _authorization(
                user_id=uuid4(),
                capabilities={"platformjobs.read"},
                boundary=AuthorizationBoundary.organization(organization_id),
            ),
            job.id,
            capability="platformjobs.execute",
        )

    assert exc.value.status_code == 404
