"""The Kubernetes loops follow the Job permission, not a setting (#769 stage 5a)."""

from __future__ import annotations

from types import SimpleNamespace
import asyncio
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from src.jobs.platform.kubernetes_client import (
    KubernetesApiError,
    KubernetesConfigurationError,
)
from src.services import kubernetes_control
from src.services.kubernetes_control import REQUIRED_ACCESS, may_manage_jobs
from src.services.worker_pool.manifest import resolve_worker_job_spec


@pytest.mark.parametrize("cluster", [False, True])
async def test_external_capacity_is_the_only_selected_worker_provider(monkeypatch, cluster):
    from src.services.worker_pool import controller, external_controller
    from src.jobs.schedulers import kubernetes_jobs

    stop = asyncio.Event()

    async def wait(*args):
        await stop.wait()

    legacy_workers = AsyncMock(side_effect=wait)
    monkeypatch.setattr(controller, "worker_pool_loop", legacy_workers)
    monkeypatch.setattr(external_controller, "external_worker_pool_loop", wait)
    monkeypatch.setattr(kubernetes_jobs, "kubernetes_build_loop", wait)
    monkeypatch.setattr(kubernetes_control, "may_manage_jobs", AsyncMock(return_value=cluster))
    tasks = await kubernetes_control.start_kubernetes_loops(stop, SimpleNamespace(
        worker_capacity_url="https://capacity.invalid", worker_capacity_token=SecretStr("x" * 40),
    ))
    try:
        assert [task.get_name() for task in tasks] == ["external-worker-pool-controller"] + (
            ["kubernetes-build-controller"] if cluster else []
        )
        legacy_workers.assert_not_called()
    finally:
        stop.set()
        await asyncio.gather(*tasks)


async def test_partial_external_configuration_does_not_fall_back_to_kubernetes(monkeypatch):
    permission = AsyncMock(return_value=True)
    monkeypatch.setattr(kubernetes_control, "may_manage_jobs", permission)
    with pytest.raises(ValueError, match="both endpoint"):
        await kubernetes_control.start_kubernetes_loops(asyncio.Event(), SimpleNamespace(
            worker_capacity_url="https://capacity.invalid", worker_capacity_token=None,
        ))
    permission.assert_not_awaited()


class _Client:
    def __init__(self, denied: set[tuple[str, str, str]] | None = None, error: bool = False) -> None:
        self.denied = denied or set()
        self.error = error
        self.namespace = "bifrost"
        self.closed = False

    async def allows(self, verb: str, resource: str, group: str = "") -> bool:
        if self.error:
            raise KubernetesApiError("POST", "/reviews", 500)
        return (verb, resource, group) not in self.denied

    async def own_container(self) -> dict:
        return {
            "image": "ghcr.io/gobifrost/bifrost-api:1.5.0",
            "envFrom": [
                {"configMapRef": {"name": "bifrost-local-config"}},
                {"secretRef": {"name": "bifrost-local-secrets"}},
            ],
        }

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _unanswered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts in a process that has not asked yet."""
    monkeypatch.setattr(kubernetes_control, "_job_access", {})


def _with_client(monkeypatch: pytest.MonkeyPatch, client: _Client | None) -> list[object]:
    made: list[object] = []

    def make(_settings: object) -> _Client:
        made.append(_settings)
        if client is None:
            raise KubernetesConfigurationError("not in a cluster")
        return client

    monkeypatch.setattr(kubernetes_control, "KubernetesJobClient", make)
    return made


async def test_outside_a_cluster_the_loops_are_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_client(monkeypatch, None)
    assert await may_manage_jobs(SimpleNamespace()) is False


async def test_every_required_verb_must_be_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    for missing in REQUIRED_ACCESS:
        monkeypatch.setattr(kubernetes_control, "_job_access", {})
        client = _Client(denied={missing})
        _with_client(monkeypatch, client)
        assert await may_manage_jobs(SimpleNamespace()) is False, missing
        assert client.closed

    monkeypatch.setattr(kubernetes_control, "_job_access", {})
    allowed = _Client()
    _with_client(monkeypatch, allowed)
    assert await may_manage_jobs(SimpleNamespace()) is True


async def test_a_review_the_cluster_rejects_keeps_the_loops_off(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client(error=True)
    _with_client(monkeypatch, client)
    assert await may_manage_jobs(SimpleNamespace()) is False
    assert client.closed


async def test_the_answer_is_asked_once_per_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """Five access reviews per call; the Settings page asks on every load."""
    made = _with_client(monkeypatch, _Client())
    assert await may_manage_jobs(SimpleNamespace()) is True
    assert await may_manage_jobs(SimpleNamespace()) is True
    assert len(made) == 1


async def test_an_unanswered_review_is_asked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_client(monkeypatch, _Client(error=True))
    assert await may_manage_jobs(SimpleNamespace()) is False
    _with_client(monkeypatch, _Client())
    assert await may_manage_jobs(SimpleNamespace()) is True


def _settings(**overrides: object) -> SimpleNamespace:
    values = {
        "kubernetes_job_image": None,
        "kubernetes_job_configmap": None,
        "kubernetes_job_secret": None,
        "kubernetes_job_service_account": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def test_worker_jobs_copy_the_controllers_pod_by_default() -> None:
    """An install names its configmap and secret as it likes (Kind: bifrost-local-*)."""
    spec = await resolve_worker_job_spec(_Client(), _settings())  # type: ignore[arg-type]

    assert (spec.namespace, spec.image, spec.service_account) == (
        "bifrost",
        "ghcr.io/gobifrost/bifrost-api:1.5.0",
        "default",
    )
    assert spec.env_from == (
        {"configMapRef": {"name": "bifrost-local-config"}},
        {"secretRef": {"name": "bifrost-local-secrets"}},
    )


async def test_job_identity_settings_override_the_worker_job_defaults() -> None:
    spec = await resolve_worker_job_spec(
        _Client(),  # type: ignore[arg-type]
        _settings(
            kubernetes_job_image="registry/api:pinned",
            kubernetes_job_configmap="bifrost-config",
            kubernetes_job_secret="bifrost-secrets",
            kubernetes_job_service_account="bifrost-build-runner",
        ),
    )

    assert (spec.image, spec.service_account) == ("registry/api:pinned", "bifrost-build-runner")
    assert spec.env_from == (
        {"configMapRef": {"name": "bifrost-config"}},
        {"secretRef": {"name": "bifrost-secrets"}},
    )
