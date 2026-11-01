"""Provider-fixed resource bounds apply equally to saves and admission."""

from dataclasses import replace

import pytest

from shared.models import WorkerCapacityProfile
from src.services.kubernetes_execution import KubernetesPolicy
from src.services.worker_pool.external_policy import validate_external_policy

PROFILE = WorkerCapacityProfile(
    protocol_version=1, scope="test-provider", worker_version="test-worker",
    memory_limit_bytes=4096 * 2**20, max_workers=2, max_lifetime_seconds=900,
)
POLICY = KubernetesPolicy(max_workers=2, worker_memory_mib=4096, largest_worker_mib=4096)


def test_matching_provider_limits_are_accepted():
    validate_external_policy(POLICY, PROFILE, max_lifetime_seconds=900)


@pytest.mark.parametrize(("changes", "lifetime", "message"), [
    ({"worker_memory_mib": 2048}, 900, "worker_memory_mib.*fixed worker memory"),
    ({"largest_worker_mib": 8192}, 900, "largest_worker_mib.*fixed worker memory"),
    ({"max_workers": 3}, 900, "max_workers.*provider limit"),
    ({}, 901, "BIFROST_POOL_MAX_LIFETIME_SECONDS.*provider limit"),
])
def test_incompatible_provider_limits_are_rejected(changes, lifetime, message):
    with pytest.raises(ValueError, match=message):
        validate_external_policy(replace(POLICY, **changes), PROFILE, max_lifetime_seconds=lifetime)
