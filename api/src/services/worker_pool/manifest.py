"""Kubernetes Job manifest for one reusable worker incarnation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from src.jobs.platform.kubernetes_client import (
    KubernetesConfigurationError,
    KubernetesJobClient,
    memory_resources,
)

WORKER_LABEL = "bifrost-pool-worker"
INCARNATION_LABEL = "bifrost.gobifrost.com/worker-incarnation"
# Finished worker Jobs are history only; the incarnation row keeps the record.
WORKER_JOB_TTL_SECONDS = 600


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

    The pod runs the ordinary worker runtime in pool mode, with the worker
    Deployment's credentials and no Kubernetes token. It has no deadline: a
    reusable worker drains itself at its idle timeout or maximum lifetime
    and is never stopped mid-run by a Job deadline.
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
                "metadata": {"labels": labels},
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
                            "command": ["python", "-m", "src.worker.pool_main"],
                            "envFrom": [dict(source) for source in spec.env_from],
                            "env": [
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
