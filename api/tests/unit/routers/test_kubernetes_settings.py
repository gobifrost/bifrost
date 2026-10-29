"""PUT /api/admin/kubernetes/policy: partial updates, validated as a whole."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.principal import UserPrincipal
from src.models.contracts.kubernetes import KubernetesPolicyUpdate
from src.routers import kubernetes as router


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> UserPrincipal:
    monkeypatch.setattr(router, "authorize_operation", AsyncMock())
    return UserPrincipal(user_id=uuid4(), email="admin@example.com", organization_id=None)


@pytest.fixture
def uncommitted(db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncSession:
    """The route commits; keep the change inside the test's transaction."""
    monkeypatch.setattr(db_session, "commit", db_session.flush)
    return db_session


async def test_a_partial_update_changes_only_what_it_names(
    admin: UserPrincipal, uncommitted: AsyncSession
) -> None:
    page = await router.update_kubernetes_policy(
        KubernetesPolicyUpdate(max_workers=9, isolated_jobs_enabled=True),
        uncommitted,
        admin,  # type: ignore[arg-type]
    )

    assert page.policy.max_workers == 9
    assert page.policy.isolated_jobs_enabled is True
    assert page.policy.warm_workers == 1


async def test_an_inconsistent_update_is_rejected(
    admin: UserPrincipal, uncommitted: AsyncSession
) -> None:
    with pytest.raises(HTTPException) as raised:
        await router.update_kubernetes_policy(
            KubernetesPolicyUpdate(worker_memory_mib=4096, largest_worker_mib=2048),
            uncommitted,
            admin,  # type: ignore[arg-type]
        )
    assert raised.value.status_code == 422


@pytest.mark.parametrize(
    "values",
    [
        {"warm_workers": -1},
        {"max_workers": 0},
        {"worker_memory_mib": 128},
        {"idle_seconds": 0},
        {"max_isolated_pods": 101},
    ],
)
def test_out_of_range_values_fail_request_validation(values: dict[str, int]) -> None:
    with pytest.raises(ValidationError):
        KubernetesPolicyUpdate(**values)
