"""The Kubernetes control loops, in every process allowed to run them.

The on-demand worker controller and the build-Job controller start wherever
this process's service account may manage Jobs: the API when the base
manifests bind it the controller Role, or the scheduler container's executor
on an install that bound the Role to the scheduler. No setting turns them on;
outside a cluster they are simply off. Both loops tolerate several replicas.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.jobs.platform.kubernetes_client import (
    KubernetesApiError,
    KubernetesConfigurationError,
    KubernetesJobClient,
)

logger = logging.getLogger(__name__)

# What the build loop and the worker controller do with Jobs and pods.
REQUIRED_ACCESS: tuple[tuple[str, str, str], ...] = (
    ("create", "jobs", "batch"),
    ("get", "jobs", "batch"),
    ("patch", "jobs", "batch"),
    ("delete", "jobs", "batch"),
    ("list", "pods", ""),
)


# This process's answer, once a cluster gave one (``may_manage_jobs``).
_job_access: dict[str, bool] = {}


async def may_manage_jobs(settings: Any) -> bool:
    """Whether this process's service account may run the Kubernetes loops.

    Answered once per process (it makes five access reviews); a review the
    cluster could not answer is asked again by the next caller.
    """
    if "answer" in _job_access:
        return _job_access["answer"]
    try:
        client = KubernetesJobClient(settings)
    except KubernetesConfigurationError:
        _job_access["answer"] = False  # Not running in a cluster.
        return False
    try:
        for verb, resource, group in REQUIRED_ACCESS:
            if not await client.allows(verb, resource, group):
                _job_access["answer"] = False
                return False
        _job_access["answer"] = True
        return True
    except KubernetesApiError:
        logger.warning("Could not review Kubernetes Job permissions", exc_info=True)
        return False
    finally:
        await client.close()


async def start_kubernetes_loops(
    stop_event: asyncio.Event, settings: Any
) -> list[asyncio.Task[None]]:
    """Start the loops this process may run; none outside a permitted cluster."""
    if not await may_manage_jobs(settings):
        return []
    from src.jobs.schedulers.kubernetes_jobs import kubernetes_build_loop
    from src.services.worker_pool.controller import worker_pool_loop

    tasks = [
        asyncio.create_task(
            worker_pool_loop(stop_event, settings), name="worker-pool-controller"
        ),
        asyncio.create_task(
            kubernetes_build_loop(stop_event), name="kubernetes-build-controller"
        ),
    ]
    logger.info(
        "Kubernetes control loops started",
        extra={"loops": [task.get_name() for task in tasks]},
    )
    return tasks


__all__ = ["REQUIRED_ACCESS", "may_manage_jobs", "start_kubernetes_loops"]
