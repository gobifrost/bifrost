"""Kubernetes REST client and Job manifest rendering for remote platform jobs."""

from __future__ import annotations

import hashlib
import os
import ssl
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx

from src.config import get_settings
from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob

SERVICE_ACCOUNT_ROOT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
SERVICE_ACCOUNT_TOKEN_PATH = SERVICE_ACCOUNT_ROOT / "token"
SERVICE_ACCOUNT_CA_PATH = SERVICE_ACCOUNT_ROOT / "ca.crt"

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


def _required_setting(settings: Any, name: str) -> Any:
    value = getattr(settings, name, None)
    if value is None or value == "":
        raise KubernetesConfigurationError(f"Missing required setting: {name}")
    return value


def _validate_required_settings(settings: Any) -> str:
    namespace = str(_required_setting(settings, "kubernetes_build_namespace"))
    _required_setting(settings, "kubernetes_build_image")
    _required_setting(settings, "kubernetes_build_configmap")
    _required_setting(settings, "kubernetes_build_secret")
    _required_setting(settings, "kubernetes_build_service_account")
    return namespace


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
    settings: Any | None = None,
) -> dict[str, Any]:
    """Render the suspended Kubernetes Job for one claimed platform build."""

    settings = settings or get_settings()
    namespace = _required_setting(settings, "kubernetes_build_namespace")
    image = _required_setting(settings, "kubernetes_build_image")
    configmap = _required_setting(settings, "kubernetes_build_configmap")
    secret = _required_setting(settings, "kubernetes_build_secret")
    service_account = _required_setting(settings, "kubernetes_build_service_account")
    memory_request_mib = int(settings.kubernetes_build_memory_request_mib)
    memory_limit_mib = int(settings.kubernetes_build_memory_limit_mib)
    if memory_request_mib > memory_limit_mib:
        raise KubernetesConfigurationError(
            "kubernetes_build_memory_request_mib must not exceed "
            "kubernetes_build_memory_limit_mib"
        )
    cpu_request = str(settings.kubernetes_build_cpu_request)
    cpu_limit = str(settings.kubernetes_build_cpu_limit)
    pending_timeout = int(settings.kubernetes_build_pending_timeout_seconds)

    memory_request = f"{memory_request_mib}Mi"
    memory_limit = f"{memory_limit_mib}Mi"
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
            "namespace": namespace,
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
                    "serviceAccountName": service_account,
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
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": [
                                "python",
                                "-m",
                                "src.jobs.platform.kubernetes_runner",
                                str(claim.id),
                                str(claim.lease_token),
                            ],
                            "envFrom": [
                                {"configMapRef": {"name": configmap}},
                                {"secretRef": {"name": secret}},
                            ],
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
    ) -> None:
        self.settings = settings or get_settings()
        self.namespace = _validate_required_settings(self.settings)
        self._owned_client = http_client is None
        self._client = http_client or self._create_http_client()

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

    async def close(self) -> None:
        if self._owned_client:
            await self._client.aclose()
