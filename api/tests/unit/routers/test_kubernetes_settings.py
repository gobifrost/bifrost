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
from src.services.worker_pool.external_client import CapacityProviderUnavailable


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


async def test_unavailable_provider_returns_503_without_committing(
    admin: UserPrincipal, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = "Cannot verify capacity provider limits; worker settings were not saved"
    monkeypatch.setattr(router.KubernetesExecutionService, "update_policy", AsyncMock(
        side_effect=CapacityProviderUnavailable(message),
    ))
    commit = AsyncMock()
    monkeypatch.setattr(db_session, "commit", commit)
    with pytest.raises(HTTPException) as error:
        await router.update_kubernetes_policy(
            KubernetesPolicyUpdate(max_workers=2), db_session, admin,  # type: ignore[arg-type]
        )
    assert error.value.status_code == 503
    assert error.value.detail == message
    commit.assert_not_awaited()


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


class _Secret:
    def __init__(self, value: str) -> None:
        self._value = value

    def get_secret_value(self) -> str:
        return self._value


def _capacity_settings(**overrides: object) -> object:
    from types import SimpleNamespace

    base: dict[str, object] = {
        "worker_capacity_url": None,
        "worker_capacity_token": None,
        "pool_max_lifetime_seconds": 21600,
        "pool_worker_memory_request_mib": 512,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _capacity_profile() -> object:
    from shared.models import WorkerCapacityProfile

    return WorkerCapacityProfile(
        protocol_version=1,
        scope="test-scope",
        worker_version="test-worker",
        memory_limit_bytes=4096 * 1024 * 1024,
        max_workers=6,
        max_lifetime_seconds=21600,
    )


class _FakeCapacityClient:
    behaviour: object = None

    def __init__(self, url: str, token: str, http: object = None) -> None:
        self.url = url
        self.token = token

    async def profile(self) -> object:
        behaviour = _FakeCapacityClient.behaviour
        if isinstance(behaviour, Exception):
            raise behaviour
        return behaviour


def _stub_capacity(
    monkeypatch: pytest.MonkeyPatch, settings: object, behaviour: object
) -> None:
    import src.services.kubernetes_execution as service

    monkeypatch.setattr("src.config.get_settings", lambda: settings)
    _FakeCapacityClient.behaviour = behaviour
    monkeypatch.setattr(service, "ExternalCapacityClient", _FakeCapacityClient)


async def test_capacity_unconfigured_reports_no_provider(
    admin: UserPrincipal, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*args: object, **kwargs: object) -> object:
        raise AssertionError("no provider call without configuration")

    import src.services.kubernetes_execution as service

    monkeypatch.setattr("src.config.get_settings", lambda: _capacity_settings())
    monkeypatch.setattr(service, "ExternalCapacityClient", _fail)

    page = await router.get_worker_capacity_status(
        db_session, admin,  # type: ignore[arg-type]
    )

    assert page.configured is False
    assert page.endpoint is None
    assert page.bounds is None
    assert page.error is None
    assert page.policy_mismatches == []
    assert page.workers.ready == 0


async def test_capacity_reports_bounds_and_worker_states_when_policy_fits(
    admin: UserPrincipal, uncommitted: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.models.orm.execution_workers import ExecutionWorker
    from src.services.kubernetes_execution import KubernetesExecutionService

    _stub_capacity(
        monkeypatch,
        _capacity_settings(
            worker_capacity_url="https://capacity.example.invalid",
            worker_capacity_token=_Secret("synthetic-token"),
        ),
        _capacity_profile(),
    )
    await KubernetesExecutionService(uncommitted).update_policy(
        {"worker_memory_mib": 4096, "largest_worker_mib": 4096}, updated_by="test"
    )
    uncommitted.add_all(
        [
            ExecutionWorker(form="external", state="ready", capacity_scope="test-scope"),
            ExecutionWorker(form="external", state="starting", capacity_scope="test-scope"),
            ExecutionWorker(form="external", state="ready", capacity_scope="other-scope"),
            ExecutionWorker(form="job", state="ready", capacity_scope=None),
        ]
    )
    await uncommitted.flush()

    page = await router.get_worker_capacity_status(
        uncommitted, admin,  # type: ignore[arg-type]
    )

    assert page.configured is True
    assert page.endpoint == "https://capacity.example.invalid"
    assert page.error is None
    assert page.policy_mismatches == []
    assert page.bounds is not None
    assert page.bounds.scope == "test-scope"
    assert page.bounds.memory_limit_bytes == 4096 * 1024 * 1024
    assert (page.workers.ready, page.workers.starting) == (1, 1)


async def test_capacity_reports_policy_mismatch_without_saving(
    admin: UserPrincipal, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_capacity(
        monkeypatch,
        _capacity_settings(
            worker_capacity_url="https://capacity.example.invalid",
            worker_capacity_token=_Secret("synthetic-token"),
        ),
        _capacity_profile(),
    )

    page = await router.get_worker_capacity_status(
        db_session, admin,  # type: ignore[arg-type]
    )

    assert page.configured is True
    assert page.bounds is not None
    assert page.error is None
    assert len(page.policy_mismatches) == 1
    assert "4096" in page.policy_mismatches[0]


async def test_capacity_reports_provider_outage_without_raising(
    admin: UserPrincipal, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    _stub_capacity(
        monkeypatch,
        _capacity_settings(
            worker_capacity_url="https://capacity.example.invalid",
            worker_capacity_token=_Secret("synthetic-token"),
        ),
        httpx.ConnectError("synthetic outage"),
    )

    page = await router.get_worker_capacity_status(
        db_session, admin,  # type: ignore[arg-type]
    )

    assert page.configured is True
    assert page.bounds is None
    assert page.error == "Capacity provider is unreachable"


async def test_capacity_reports_invalid_provider_response(
    admin: UserPrincipal, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from src.services.worker_pool.external_client import CapacityProtocolError

    _stub_capacity(
        monkeypatch,
        _capacity_settings(
            worker_capacity_url="https://capacity.example.invalid",
            worker_capacity_token=_Secret("synthetic-token"),
        ),
        CapacityProtocolError("synthetic mismatch"),
    )

    page = await router.get_worker_capacity_status(
        db_session, admin,  # type: ignore[arg-type]
    )

    assert page.error == "Capacity provider response is invalid"


async def test_capacity_reports_misconfigured_endpoint(
    admin: UserPrincipal, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_capacity(
        monkeypatch,
        _capacity_settings(
            worker_capacity_url="https://capacity.example.invalid",
            worker_capacity_token=_Secret("synthetic-token"),
        ),
        ValueError("synthetic misconfiguration"),
    )

    page = await router.get_worker_capacity_status(
        db_session, admin,  # type: ignore[arg-type]
    )

    assert page.configured is True
    assert page.bounds is None
    assert page.error == "Capacity provider endpoint is invalid"
