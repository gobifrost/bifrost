"""The Kubernetes loops follow the Job permission, not a setting (#769 stage 5a)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.jobs.platform.kubernetes_client import (
    KubernetesApiError,
    KubernetesConfigurationError,
)
from src.services import kubernetes_control
from src.services.kubernetes_control import REQUIRED_ACCESS, may_manage_jobs
from src.services.worker_pool.manifest import resolve_worker_job_spec


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
