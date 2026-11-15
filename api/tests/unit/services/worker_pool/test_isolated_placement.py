"""Isolation preserves the selected local/Cloudflare capacity preference."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shared.models import CloudflareCapacityRules
from src.jobs.platform.application_deploy import APPLICATION_DEPLOY_DEFINITION
from src.jobs.platform.policy import EXTERNAL_ISOLATED_BACKEND
from src.services.kubernetes_execution import KubernetesPolicy, decide_execution_backend
from src.services.worker_pool.connection import CapacityConnection


@pytest.mark.asyncio
@pytest.mark.parametrize("kubernetes,rules,expected", [
    (True, CloudflareCapacityRules(), EXTERNAL_ISOLATED_BACKEND),
    (True, CloudflareCapacityRules(prefer_kubernetes=True), "kubernetes"),
    (True, CloudflareCapacityRules(app_builds=False), "kubernetes"),
    (False, CloudflareCapacityRules(app_builds=False, prefer_kubernetes=True), EXTERNAL_ISOLATED_BACKEND),
])
async def test_new_isolated_capacity_uses_existing_overflow_preference(
    monkeypatch, kubernetes, rules, expected,
) -> None:
    monkeypatch.setattr("src.services.kubernetes_execution.load_kubernetes_policy", AsyncMock(
        return_value=KubernetesPolicy(
            isolated_jobs_enabled=True, enabled_job_types=frozenset({"application.deploy"}),
        ),
    ))
    monkeypatch.setattr("src.services.worker_pool.connection.load_capacity_provider_connection", AsyncMock(
        return_value=CapacityConnection("https://capacity.example.test", "fixture-token", rules=rules),
    ))
    monkeypatch.setattr("src.services.kubernetes_control.may_manage_jobs", AsyncMock(return_value=kubernetes))

    result = await decide_execution_backend(
        SimpleNamespace(scalar=AsyncMock(return_value=None)), APPLICATION_DEPLOY_DEFINITION,
    )

    assert result == expected


@pytest.mark.asyncio
async def test_isolation_without_cloud_configuration_retains_kubernetes(monkeypatch) -> None:
    monkeypatch.setattr("src.services.kubernetes_execution.load_kubernetes_policy", AsyncMock(
        return_value=KubernetesPolicy(
            isolated_jobs_enabled=True, enabled_job_types=frozenset({"application.deploy"}),
        ),
    ))
    monkeypatch.setattr("src.services.worker_pool.connection.load_capacity_provider_connection", AsyncMock(return_value=None))
    assert await decide_execution_backend(SimpleNamespace(), APPLICATION_DEPLOY_DEFINITION) == "kubernetes"
