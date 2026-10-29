"""Kubernetes Job manifest for one reusable worker incarnation."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from src.jobs.platform.kubernetes_client import (
    KubernetesApiError,
    KubernetesConfigurationError,
    KubernetesJobClient,
    memory_resources,
)

logger = logging.getLogger(__name__)

WORKER_LABEL = "bifrost-pool-worker"
INCARNATION_LABEL = "bifrost.gobifrost.com/worker-incarnation"
# The cluster autoscaler may evict a worker only while the controller marks it
# idle (``WorkerPoolController._mark_evictable``); a new worker starts unmarked.
SAFE_TO_EVICT = "cluster-autoscaler.kubernetes.io/safe-to-evict"
# Finished worker Jobs are history only; the incarnation row keeps the record.
WORKER_JOB_TTL_SECONDS = 600
# Backoff between attempts to read the Job identity when the Kubernetes API
# fails: it doubles from the first wait up to the longest.
SPEC_RETRY_INITIAL_SECONDS = 2.0
SPEC_RETRY_MAX_SECONDS = 60.0


def worker_job_name(incarnation_id: UUID) -> str:
    return f"bifrost-worker-{incarnation_id.hex[:24]}"


@dataclass(frozen=True)
class WorkerJobSpec:
    """Where and as what a Job runs: worker Jobs and isolated pods alike."""

    namespace: str
    image: str
    # The container's environment sources (configmap and secret references).
    env_from: tuple[dict[str, Any], ...]
    service_account: str
    # The controller container's own policy when Jobs run its image, so a
    # moving tag (``:latest``) is pulled again instead of read from a node's
    # cache.
    image_pull_policy: str = "IfNotPresent"


async def resolve_worker_job_spec(client: KubernetesJobClient, settings: Any) -> WorkerJobSpec:
    """The Job identity settings when set; otherwise a copy of this controller's pod.

    Worker Jobs and isolated pods both run as this. An install with no Job
    identity settings runs them in the controller's own namespace, image
    (and pull policy) and environment sources (the configmap and secret its
    API pod reads), with the namespace's ``default`` service account (the
    pod mounts no token).
    """
    own: dict[str, Any] = {}
    if not (
        settings.kubernetes_job_image
        and settings.kubernetes_job_configmap
        and settings.kubernetes_job_secret
    ):
        own = await client.own_container() or {}
    image = settings.kubernetes_job_image or own.get("image")
    if not image:
        raise KubernetesConfigurationError("No image is known for Jobs")
    if settings.kubernetes_job_configmap and settings.kubernetes_job_secret:
        env_from: tuple[dict[str, Any], ...] = (
            {"configMapRef": {"name": settings.kubernetes_job_configmap}},
            {"secretRef": {"name": settings.kubernetes_job_secret}},
        )
    else:
        env_from = tuple(own.get("envFrom") or ())
    if not env_from:
        raise KubernetesConfigurationError("No environment is known for Jobs")
    return WorkerJobSpec(
        namespace=client.namespace,
        image=image,
        env_from=env_from,
        service_account=settings.kubernetes_job_service_account or "default",
        image_pull_policy=(
            "IfNotPresent"
            if settings.kubernetes_job_image
            else own.get("imagePullPolicy") or "IfNotPresent"
        ),
    )


async def resolve_job_spec_when_ready(
    client: KubernetesJobClient, settings: Any, stop_event: asyncio.Event, controller: str
) -> WorkerJobSpec | None:
    """The Job identity for a controller loop, resolved once and kept.

    A Kubernetes API failure (an error status, or no answer) is retried
    with backoff, so a transient outage at start does not disable the
    controller until a restart. An identity no Job could run with (no image
    or environment) needs a configuration change and a restart: the loop
    idles. None when the loop stops first.
    """
    delay = SPEC_RETRY_INITIAL_SECONDS
    while True:
        try:
            return await resolve_worker_job_spec(client, settings)
        except KubernetesConfigurationError as exc:
            logger.error("%s disabled: %s", controller, exc)
            await stop_event.wait()
            return None
        except (KubernetesApiError, httpx.HTTPError) as exc:
            logger.warning(
                "%s could not read its Job identity (%s: %s); retrying in %.0f seconds",
                controller,
                type(exc).__name__,
                exc,
                delay,
            )
        with suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        if stop_event.is_set():
            return None
        delay = min(delay * 2, SPEC_RETRY_MAX_SECONDS)


def build_worker_job_manifest(
    incarnation_id: UUID,
    settings: Any,
    spec: WorkerJobSpec,
    *,
    memory_limit_bytes: int,
    sized: bool,
) -> dict[str, Any]:
    """Render the suspended Job for one reusable worker.

    ``memory_limit_bytes`` is the limit its row recorded when it was
    reserved. A standard worker requests the deployment's memory request,
    which must fit that limit. A worker sized for one large request
    requests its whole limit, so it is only scheduled where that memory
    exists.

    The pod runs the worker entrypoint, which hosts a physical remote launcher,
    with the worker Deployment's credentials and no Kubernetes token. It has
    no deadline: a reusable worker drains itself at its idle timeout or maximum
    lifetime and is never stopped mid-run by a Job deadline.
    """
    namespace = spec.namespace
    image = spec.image
    service_account = spec.service_account
    memory_request, memory_limit = memory_resources(
        memory_limit_bytes,
        sized=sized,
        standard_request_mib=settings.pool_worker_memory_request_mib,
    )
    labels = {
        "app.kubernetes.io/name": WORKER_LABEL,
        INCARNATION_LABEL: str(incarnation_id),
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": worker_job_name(incarnation_id),
            "namespace": namespace,
            "labels": labels,
        },
        "spec": {
            "suspend": True,
            "backoffLimit": 0,
            "parallelism": 1,
            "completions": 1,
            "ttlSecondsAfterFinished": WORKER_JOB_TTL_SECONDS,
            "template": {
                "metadata": {"labels": labels, "annotations": {SAFE_TO_EVICT: "false"}},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": service_account,
                    "automountServiceAccountToken": False,
                    # Matches the worker Deployment's drain budget.
                    "terminationGracePeriodSeconds": 360,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 1000,
                        "runAsGroup": 1000,
                        "fsGroup": 1000,
                    },
                    "containers": [
                        {
                            "name": "worker",
                            "image": image,
                            "imagePullPolicy": spec.image_pull_policy,
                            "command": ["python", "-m", "src.worker.main"],
                            "envFrom": [dict(source) for source in spec.env_from],
                            "env": [
                                {"name": "BIFROST_API_URL", "value": "http://api:8000"},
                                {"name": "BIFROST_WORKER_INCARNATION_ID", "value": str(incarnation_id)},
                                # The controller's lifetime, so a worker never
                                # disagrees with the controller that launched it.
                                # Warm workers and Idle time it reads from
                                # Settings -> Kubernetes, so a change applies
                                # to running workers too.
                                {
                                    "name": "BIFROST_POOL_MAX_LIFETIME_SECONDS",
                                    "value": str(settings.pool_max_lifetime_seconds),
                                },
                                {"name": "BIFROST_DATABASE_POOL_SIZE", "value": "1"},
                                {"name": "BIFROST_DATABASE_MAX_OVERFLOW", "value": "2"},
                                {"name": "PIP_NO_CACHE_DIR", "value": "1"},
                                {"name": "HOME", "value": "/home/bifrost"},
                                {
                                    "name": "BIFROST_KUBERNETES_JOB_UID",
                                    "valueFrom": {
                                        "fieldRef": {
                                            "fieldPath": (
                                                "metadata.labels['batch.kubernetes.io/"
                                                "controller-uid']"
                                            )
                                        }
                                    },
                                },
                                {
                                    "name": "BIFROST_KUBERNETES_POD_UID",
                                    "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}},
                                },
                            ],
                            "resources": {
                                "requests": {
                                    "memory": memory_request,
                                    "cpu": str(settings.pool_worker_cpu_request),
                                },
                                "limits": {
                                    "memory": memory_limit,
                                    "cpu": str(settings.pool_worker_cpu_limit),
                                },
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "volumeMounts": [
                                {"name": "home", "mountPath": "/home/bifrost"},
                                {"name": "tmp", "mountPath": "/tmp"},
                                {"name": "workspace", "mountPath": "/workspace"},
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "home", "emptyDir": {}},
                        {"name": "tmp", "emptyDir": {}},
                        {"name": "workspace", "emptyDir": {}},
                    ],
                },
            },
        },
    }


__all__ = [
    "INCARNATION_LABEL",
    "WORKER_LABEL",
    "WorkerJobSpec",
    "build_worker_job_manifest",
    "resolve_worker_job_spec",
    "worker_job_name",
]
