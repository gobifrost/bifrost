"""Deployment variables this release no longer reads, or reads under a new name.

``Settings`` ignores unknown variables, so a variable an operator kept after
it was retired is silently dropped. The API names them at startup (API only:
worker Jobs copy the API pod's environment), and Settings -> Kubernetes shows
the unread list until the variables are removed.

Lookups are case-insensitive, as ``Settings`` is, and a variable set to an
empty value counts as unset. The ``.env`` file is not consulted: deployments
set these in the process environment.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

logger = logging.getLogger(__name__)

#: Settings -> Kubernetes field -> the variable it is imported from once
#: (src.services.kubernetes_execution.import_kubernetes_policy_from_env).
KUBERNETES_POLICY_VARIABLES: dict[str, str] = {
    "warm_workers": "BIFROST_POOL_MIN_WORKERS",
    "max_workers": "BIFROST_POOL_MAX_WORKERS",
    "worker_memory_mib": "BIFROST_POOL_WORKER_MEMORY_LIMIT_MIB",
    "largest_worker_mib": "BIFROST_POOL_WORKER_MAX_MEMORY_MIB",
    "idle_seconds": "BIFROST_POOL_IDLE_SECONDS",
    "isolated_jobs_enabled": "BIFROST_PLATFORM_BUILD_BACKEND",
    "max_isolated_pods": "BIFROST_KUBERNETES_BUILD_MAX_JOBS",
    "enabled_job_types": "BIFROST_KUBERNETES_BUILD_JOB_TYPES",
}

#: Removed without a replacement: isolated pods are sized like worker Jobs.
REMOVED_VARIABLES = (
    "BIFROST_KUBERNETES_BUILD_MEMORY_REQUEST_MIB",
    "BIFROST_KUBERNETES_BUILD_MEMORY_LIMIT_MIB",
)

#: Retired by the unified runtime (v1.4.1 and earlier).
RETIRED_VARIABLES = (
    "BIFROST_MAX_CONCURRENCY",
    "BIFROST_MAX_WORKERS",
    "BIFROST_MAX_SERVICE_WORKERS",
)

#: Read until the legacy broker drain is recorded (src.services.legacy_broker_drain).
LEGACY_BROKER_VARIABLE = "BIFROST_RABBITMQ_URL"

#: Old name -> new name. ``Settings`` reads the new name first, then the old.
RENAMED_VARIABLES: dict[str, str] = {
    f"BIFROST_KUBERNETES_BUILD_{suffix}": f"BIFROST_KUBERNETES_JOB_{suffix}"
    for suffix in (
        "NAMESPACE",
        "IMAGE",
        "CONFIGMAP",
        "SECRET",
        "SERVICE_ACCOUNT",
        "CPU_REQUEST",
        "CPU_LIMIT",
        "PENDING_TIMEOUT_SECONDS",
    )
}


def set_variables(environ: Mapping[str, str]) -> dict[str, str]:
    """The variables set to a non-empty value, by upper-case name."""
    return {name.upper(): value for name, value in environ.items() if value.strip()}


def unread_environment_variables(
    environ: Mapping[str, str], *, broker_drained: bool
) -> list[str]:
    """Variables set in ``environ`` that this release does not read, sorted."""
    unread = {
        *KUBERNETES_POLICY_VARIABLES.values(),
        *REMOVED_VARIABLES,
        *RETIRED_VARIABLES,
    }
    if broker_drained:
        unread.add(LEGACY_BROKER_VARIABLE)
    return sorted(unread & set_variables(environ).keys())


def renamed_environment_variables(environ: Mapping[str, str]) -> list[tuple[str, str]]:
    """``(old, new)`` for each old name that supplies a value, sorted."""
    present = set_variables(environ)
    return sorted(
        (old, new)
        for old, new in RENAMED_VARIABLES.items()
        if old in present and new not in present
    )


def warn_about_environment(environ: Mapping[str, str], *, broker_drained: bool) -> None:
    """One startup warning per list, when it is not empty."""
    unread = unread_environment_variables(environ, broker_drained=broker_drained)
    if unread:
        logger.warning(
            "These environment variables are no longer used; remove them: %s",
            ", ".join(unread),
        )
    renamed = renamed_environment_variables(environ)
    if renamed:
        logger.warning(
            "These environment variables were renamed; the old names stop "
            "working in a later release: %s",
            ", ".join(f"{old} -> {new}" for old, new in renamed),
        )
