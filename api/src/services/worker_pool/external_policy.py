"""The same provider limits apply at policy save and physical admission."""

from typing import TYPE_CHECKING

from shared.models import WorkerCapacityProfile

if TYPE_CHECKING:
    from src.services.kubernetes_execution import KubernetesPolicy


def validate_external_policy(
    policy: "KubernetesPolicy", profile: WorkerCapacityProfile, *, max_lifetime_seconds: int,
    hybrid: bool = False,
) -> None:
    """Reject an incompatible policy without changing it or provisioning work."""
    if "agent.run" in profile.execution_handlers and "agent.loop.v1" not in profile.compute_capabilities:
        raise ValueError("agent.run requires the API-only agent.loop.v1 compute capability")
    if (any(handler in profile.execution_handlers for handler in ("agent.summarize", "agent.summarize_backfill"))
            and "summary.compute.v1" not in profile.compute_capabilities):
        raise ValueError("Agent summaries require the API-only summary.compute.v1 compute capability")
    if max_lifetime_seconds > profile.max_lifetime_seconds:
        raise ValueError(
            "BIFROST_POOL_MAX_LIFETIME_SECONDS exceeds the capacity provider limit "
            f"({profile.max_lifetime_seconds}); update the deployment configuration"
        )
    if policy.reserved_fast_path_workers and not hybrid:
        raise ValueError(
            "reserved_fast_path_workers requires managed Kubernetes or static Fast Path workers; "
            "the external capacity provider does not advertise lane isolation"
        )
