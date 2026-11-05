"""The same provider limits apply at policy save and physical admission."""

from typing import TYPE_CHECKING

from shared.models import WorkerCapacityProfile

if TYPE_CHECKING:
    from src.services.kubernetes_execution import KubernetesPolicy


def validate_external_policy(
    policy: "KubernetesPolicy", profile: WorkerCapacityProfile, *, max_lifetime_seconds: int,
) -> None:
    """Reject an incompatible policy without changing it or provisioning work."""
    for field in ("worker_memory_mib", "largest_worker_mib"):
        if getattr(policy, field) * 2**20 != profile.memory_limit_bytes:
            raise ValueError(
                f"{field} must match the capacity provider's fixed worker memory "
                f"({profile.memory_limit_bytes // 2**20} MiB)"
            )
    if policy.max_workers > profile.max_workers:
        raise ValueError(f"max_workers must not exceed the capacity provider limit ({profile.max_workers})")
    if max_lifetime_seconds > profile.max_lifetime_seconds:
        raise ValueError(
            "BIFROST_POOL_MAX_LIFETIME_SECONDS exceeds the capacity provider limit "
            f"({profile.max_lifetime_seconds}); update the deployment configuration"
        )
