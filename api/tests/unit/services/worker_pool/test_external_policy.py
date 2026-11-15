"""Provider-fixed resource bounds apply equally to saves and admission."""

from dataclasses import replace

import pytest

from shared.models import WorkerCapacityProfile
from src.services.kubernetes_execution import KubernetesPolicy
from src.services.worker_pool.external_policy import validate_external_policy

PROFILE = WorkerCapacityProfile(
    protocol_version=2, scope="test-provider", worker_version="test-worker",
    memory_limit_bytes=4096 * 2**20, max_workers=2, max_lifetime_seconds=900,
    execution_handlers=["workflow.execute"], compute_capabilities=[],
)
POLICY = KubernetesPolicy(max_workers=2, worker_memory_mib=4096, largest_worker_mib=4096)


def test_matching_provider_limits_are_accepted():
    validate_external_policy(POLICY, PROFILE, max_lifetime_seconds=900)


@pytest.mark.parametrize(("changes", "lifetime", "message"), [
    ({}, 901, "BIFROST_POOL_MAX_LIFETIME_SECONDS.*provider limit"),
])
def test_incompatible_provider_limits_are_rejected(changes, lifetime, message):
    with pytest.raises(ValueError, match=message):
        validate_external_policy(replace(POLICY, **changes), PROFILE, max_lifetime_seconds=lifetime)


def test_provider_uses_its_own_fixed_memory_and_caps_its_own_count():
    """Kubernetes settings remain meaningful when Cloud capacity is selected."""
    validate_external_policy(
        replace(POLICY, worker_memory_mib=1024, largest_worker_mib=2048, max_workers=8),
        PROFILE, max_lifetime_seconds=900,
    )


def test_agent_handler_requires_credential_free_compute_protocol():
    profile = PROFILE.model_copy(update={"execution_handlers": ["workflow.execute", "agent.run"]})
    with pytest.raises(ValueError, match="agent.run requires.*agent.loop.v1"):
        validate_external_policy(POLICY, profile, max_lifetime_seconds=900)
    validate_external_policy(
        POLICY,
        profile.model_copy(update={"compute_capabilities": ["agent.loop.v1"]}),
        max_lifetime_seconds=900,
    )


@pytest.mark.parametrize("handler", ["agent.summarize", "agent.summarize_backfill"])
def test_summary_handlers_require_credential_free_compute_protocol(handler):
    profile = PROFILE.model_copy(update={"execution_handlers": [handler]})
    with pytest.raises(ValueError, match="summaries require.*summary.compute.v1"):
        validate_external_policy(POLICY, profile, max_lifetime_seconds=900)
    validate_external_policy(
        POLICY, profile.model_copy(update={"compute_capabilities": ["summary.compute.v1"]}),
        max_lifetime_seconds=900,
    )
