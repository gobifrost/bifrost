"""Kubernetes REST client and Job manifest rendering for remote platform jobs."""

from __future__ import annotations

import hashlib
import os
import ssl
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote
from uuid import UUID

import httpx

from src.config import get_settings
from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob

if TYPE_CHECKING:
    from src.services.worker_pool.manifest import WorkerJobSpec

SERVICE_ACCOUNT_ROOT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
SERVICE_ACCOUNT_TOKEN_PATH = SERVICE_ACCOUNT_ROOT / "token"
SERVICE_ACCOUNT_CA_PATH = SERVICE_ACCOUNT_ROOT / "ca.crt"
SERVICE_ACCOUNT_NAMESPACE_PATH = SERVICE_ACCOUNT_ROOT / "namespace"

JOB_TTL_SECONDS = 3600
JOB_DEADLINE_BUFFER_SECONDS = 60


class KubernetesConfigurationError(RuntimeError):
    """Kubernetes remote-build configuration is incomplete."""


class KubernetesApiError(RuntimeError):
    """Kubernetes API request failed without exposing response details."""

    def __init__(self, method: str, path: str, status_code: int) -> None:
        super().__init__(
            f"Kubernetes API {method} {path} failed with status {status_code}"
        )
        self.method = method
        self.path = path
        self.status_code = status_code


def in_cluster_namespace() -> str | None:
    """The namespace of this pod's service account, when running in a cluster."""
    try:
        return SERVICE_ACCOUNT_NAMESPACE_PATH.read_text().strip() or None
    except OSError:
        return None


def memory_resources(
    limit_bytes: int, *, sized: bool, standard_request_mib: int
) -> tuple[str, str]:
    """A worker Job container's memory request and limit
    (``worker_pool.sizing``). A sized Job requests its whole limit; a
    standard one the deployment's request, which must fit."""
    limit_mib = limit_bytes // 2**20
    if sized:
        return f"{limit_mib}Mi", f"{limit_mib}Mi"
    if standard_request_mib > limit_mib:
        raise KubernetesConfigurationError(
            "pool_worker_memory_request_mib must not exceed Worker memory"
        )
    return f"{standard_request_mib}Mi", f"{limit_mib}Mi"


def oom_killed(pods: list[dict[str, Any]]) -> bool:
    """Whether Kubernetes stopped any of these pods' containers for memory."""
    return any(
        container.get("state", {}).get("terminated", {}).get("reason") == "OOMKilled"
        for pod in pods
        for container in pod.get("status", {}).get("containerStatuses", [])
    )


def _lease_token_fingerprint(lease_token: UUID) -> str:
    """Return a non-credential fingerprint identifying one fenced attempt.

    The lease token is a bearer credential: presenting it authorizes progress
    and terminal updates for the attempt. Kubernetes object metadata (names
    and labels) is readable by anyone with Job/pod read access, so it must
    never carry the token itself. The full token travels only in the pod's
    startup command arguments, which the runner needs to authenticate.
    """
    return hashlib.sha256(str(lease_token).encode("utf-8")).hexdigest()[:32]


def _job_name(claim: ClaimedPlatformJob) -> str:
    return f"bifrost-job-{claim.id.hex}-{_lease_token_fingerprint(claim.lease_token)[:12]}"


def _expected_labels(manifest: dict[str, Any]) -> dict[str, Any]:
    labels = manifest.get("metadata", {}).get("labels")
    if not isinstance(labels, dict):
        raise ValueError("Job manifest metadata.labels is required")
    return labels


def _validate_adopted_job(
    manifest: dict[str, Any],
    existing: dict[str, Any],
) -> None:
    expected = _expected_labels(manifest)
    actual = existing.get("metadata", {}).get("labels", {})
    if not isinstance(actual, dict) or any(
        actual.get(key) != value for key, value in expected.items()
    ):
        raise KubernetesApiError("GET", "adopted-job-labels", 409)


def _service_host_url(host: str, port: str) -> str:
    address = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"https://{address}:{port}"


def build_job_manifest(
    claim: ClaimedPlatformJob,
    settings: Any,
    spec: WorkerJobSpec,
    *,
    memory_request_bytes: int,
    memory_limit_bytes: int,
) -> dict[str, Any]:
    """Render the suspended Kubernetes Job for one claimed platform build.

    The pod runs as ``spec`` says, the identity worker Jobs use: the Job
    identity settings when set, else a copy of the controller's own pod.
    Its memory request and limit are what its claim recorded: a standard
    pod requests the worker memory request, a sized pod its whole limit
    (``worker_pool.sizing``).
    """

    memory_request = f"{memory_request_bytes // 2**20}Mi"
    memory_limit = f"{memory_limit_bytes // 2**20}Mi"
    cpu_request = str(settings.kubernetes_job_cpu_request)
    cpu_limit = str(settings.kubernetes_job_cpu_limit)
    pending_timeout = int(settings.kubernetes_job_pending_timeout_seconds)

    name = _job_name(claim)
    labels = {
        "app.kubernetes.io/name": "bifrost-platform-job",
        "bifrost.gobifrost.com/platform-job-id": str(claim.id),
        "bifrost.gobifrost.com/lease-token-sha256": _lease_token_fingerprint(
            claim.lease_token
        ),
    }

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": spec.namespace,
            "labels": labels,
        },
        "spec": {
            "suspend": True,
            "backoffLimit": 0,
            "parallelism": 1,
            "completions": 1,
            "ttlSecondsAfterFinished": JOB_TTL_SECONDS,
            "activeDeadlineSeconds": (
                pending_timeout + claim.timeout_seconds + JOB_DEADLINE_BUFFER_SECONDS
            ),
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "restartPolicy": "Never",
                    "serviceAccountName": spec.service_account,
                    "automountServiceAccountToken": False,
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 1000,
                        "runAsGroup": 1000,
                        "fsGroup": 1000,
                    },
                    "containers": [
                        {
                            "name": "runner",
                            "image": spec.image,
                            "imagePullPolicy": spec.image_pull_policy,
                            "command": [
                                "python",
                                "-m",
                                "src.jobs.platform.kubernetes_runner",
                                str(claim.id),
                                str(claim.lease_token),
                            ],
                            "envFrom": [dict(source) for source in spec.env_from],
                            "env": [
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
                                    "valueFrom": {
                                        "fieldRef": {"fieldPath": "metadata.uid"}
                                    },
                                },
                            ],
                            "resources": {
                                "requests": {
                                    "memory": memory_request,
                                    "cpu": cpu_request,
                                },
                                "limits": {"memory": memory_limit, "cpu": cpu_limit},
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                        }
                    ],
                },
            },
        },
    }


class KubernetesJobClient:
    """Small async Kubernetes REST client for batch Job orchestration."""

    def __init__(
        self,
        settings: Any | None = None,
        http_client: httpx.AsyncClient | None = None,
        *,
        namespace: str | None = None,
    ) -> None:
        """``namespace`` defaults to the Job namespace setting, then this pod's."""
        self.settings = settings or get_settings()
        resolved = (
            namespace
            or getattr(self.settings, "kubernetes_job_namespace", None)
            or in_cluster_namespace()
        )
        if not resolved:
            raise KubernetesConfigurationError("No Kubernetes namespace is configured")
        self.namespace = str(resolved)
        self._owned_client = http_client is None
        self._client = http_client or self._create_http_client()

    async def allows(self, verb: str, resource: str, group: str = "") -> bool:
        """Whether this service account may ``verb`` ``resource`` in its namespace."""
        review = {
            "apiVersion": "authorization.k8s.io/v1",
            "kind": "SelfSubjectAccessReview",
            "spec": {
                "resourceAttributes": {
                    "namespace": self.namespace,
                    "verb": verb,
                    "group": group,
                    "resource": resource,
                }
            },
        }
        response = await self._request(
            "POST",
            "/apis/authorization.k8s.io/v1/selfsubjectaccessreviews",
            expected={201},
            json=review,
        )
        return bool(response.json().get("status", {}).get("allowed"))

    async def own_container(self) -> dict[str, Any] | None:
        """This pod's first container (the process's own image and environment)."""
        name = os.environ.get("HOSTNAME")
        if not name:
            return None
        response = await self._request(
            "GET", f"{self._pod_path()}/{quote(name, safe='')}", expected={200}
        )
        containers = response.json().get("spec", {}).get("containers") or []
        return containers[0] if containers else None

    def _create_http_client(self) -> httpx.AsyncClient:
        host = os.environ.get("KUBERNETES_SERVICE_HOST")
        port = os.environ.get("KUBERNETES_SERVICE_PORT_HTTPS")
        if not host or not port:
            raise KubernetesConfigurationError(
                "KUBERNETES_SERVICE_HOST and KUBERNETES_SERVICE_PORT_HTTPS are required"
            )
        if not SERVICE_ACCOUNT_CA_PATH.is_file():
            raise KubernetesConfigurationError("Kubernetes service-account CA is missing")
        if not SERVICE_ACCOUNT_TOKEN_PATH.is_file():
            raise KubernetesConfigurationError("Kubernetes service-account token is missing")
        ssl_context = ssl.create_default_context(cafile=str(SERVICE_ACCOUNT_CA_PATH))
        return httpx.AsyncClient(
            base_url=_service_host_url(host, port),
            verify=ssl_context,
            timeout=5.0,
        )

    def _read_token(self) -> str:
        try:
            return SERVICE_ACCOUNT_TOKEN_PATH.read_text().strip()
        except OSError as exc:
            raise KubernetesConfigurationError(
                "Kubernetes service-account token could not be read"
            ) from exc

    async def _request(
        self,
        method: str,
        path: str,
        *,
        expected: set[int],
        **kwargs: Any,
    ) -> httpx.Response:
        headers = dict(kwargs.pop("headers", {}))
        headers["Authorization"] = f"Bearer {self._read_token()}"
        response = await self._client.request(method, path, headers=headers, **kwargs)
        if response.status_code not in expected:
            raise KubernetesApiError(method, path, response.status_code)
        return response

    def _job_path(self, name: str | None = None) -> str:
        base = f"/apis/batch/v1/namespaces/{quote(self.namespace, safe='')}/jobs"
        if name is None:
            return base
        return f"{base}/{quote(name, safe='')}"

    def _pod_path(self) -> str:
        return f"/api/v1/namespaces/{quote(self.namespace, safe='')}/pods"

    async def get_job(self, name: str) -> dict[str, Any] | None:
        path = self._job_path(name)
        response = await self._request("GET", path, expected={200, 404})
        if response.status_code == 404:
            return None
        return response.json()

    async def create_job(self, manifest: dict[str, Any]) -> dict[str, Any]:
        name = manifest.get("metadata", {}).get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("Job manifest metadata.name is required")
        response = await self._request(
            "POST",
            self._job_path(),
            expected={201, 409},
            json=manifest,
        )
        if response.status_code == 409:
            existing = await self.get_job(name)
            if existing is None:
                raise KubernetesApiError("GET", self._job_path(name), 404)
            _validate_adopted_job(manifest, existing)
            return existing
        return response.json()

    async def start_job(self, name: str, uid: str) -> dict[str, Any]:
        response = await self._request(
            "PATCH",
            self._job_path(name),
            expected={200},
            headers={"Content-Type": "application/json-patch+json"},
            json=[
                {"op": "test", "path": "/metadata/uid", "value": uid},
                {"op": "replace", "path": "/spec/suspend", "value": False},
            ],
        )
        return response.json()

    async def delete_job(self, name: str, uid: str) -> None:
        await self._request(
            "DELETE",
            self._job_path(name),
            expected={200, 202, 404},
            json={
                "apiVersion": "v1",
                "kind": "DeleteOptions",
                "propagationPolicy": "Foreground",
                "preconditions": {"uid": uid},
            },
        )

    async def list_pods(self, job_uid: str) -> list[dict[str, Any]]:
        response = await self._request(
            "GET",
            self._pod_path(),
            expected={200},
            params={"labelSelector": f"batch.kubernetes.io/controller-uid={job_uid}"},
        )
        items = response.json().get("items", [])
        if not isinstance(items, list):
            raise KubernetesApiError("GET", self._pod_path(), response.status_code)
        return items

    async def annotate_pod(self, name: str, annotations: dict[str, str]) -> None:
        """Merge ``annotations`` into a pod's metadata; a pod already gone is ignored."""
        await self._request(
            "PATCH",
            f"{self._pod_path()}/{quote(name, safe='')}",
            expected={200, 404},
            headers={"Content-Type": "application/merge-patch+json"},
            json={"metadata": {"annotations": annotations}},
        )

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()
