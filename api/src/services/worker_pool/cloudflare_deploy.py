"""HTTP-only Cloudflare control-plane provisioning for capacity Workers.

The caller owns durable job state and chooses when a reconciliation happens.
This module deliberately has no subprocess, Docker, npm, or Wrangler path:
every Cloudflare mutation is made through the injected ``httpx`` client.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx


CF_API_ORIGIN = "https://api.cloudflare.com"
CF_V4_PATH = "/client/v4"
CLOUDFLARE_WORKER_ARTIFACT_DIRECTORY = Path("/app/artifacts/worker-capacity/cloudflare")
MIGRATION_TAG = "v1"
CONTAINER_CLASS = "WorkerContainer"
CLOUDFLARE_COMPATIBILITY_DATE = "2026-11-10"
CONTAINER_INSTANCE_TYPE = "standard-1"
MAX_CONFIGURED_WORKERS = 50
QUALIFIED_MEMORY_LIMIT_BYTES = 4096 * 2**20
SQLITE_DURABLE_OBJECT_CLASSES = ("WorkerLedger", "ScopeRegistry", CONTAINER_CLASS)


@dataclass(frozen=True)
class CapacityToken:
    """The Worker capacity credential; never include it in diagnostics."""

    value: str = field(repr=False)


@dataclass(frozen=True)
class CloudflareApiToken:
    """A scoped Cloudflare API token."""

    value: str = field(repr=False)


@dataclass(frozen=True)
class CloudflareDeploymentLimits:
    """Capacity settings embedded in the immutable Worker deployment."""

    memory_limit_bytes: int
    max_workers: int
    max_lifetime_seconds: int
    starting_grace_ms: int
    execution_handlers: tuple[str, ...]
    compute_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class CloudflareDeployment:
    """Trusted immutable desired state supplied by the canonical job runner."""

    account_id: str
    script_name: str
    scope: str
    bifrost_api_origin: str
    worker_version: str
    registry_image: str
    limits: CloudflareDeploymentLimits

    @property
    def container_application_name(self) -> str:
        return f"{self.script_name}-worker-container"


@dataclass(frozen=True)
class CloudflareDeploymentResult:
    application_id: str
    container_namespace_id: str
    migration_tag: str
    worker_url: str


class CloudflareProvisioningError(RuntimeError):
    """A safe, operation-level Cloudflare provisioning failure."""

    def __init__(self, step: str, *, status_code: int | None = None) -> None:
        self.step = step
        self.status_code = status_code
        suffix = f" (HTTP {status_code})" if status_code is not None else ""
        super().__init__(f"Cloudflare provisioning failed during {step}{suffix}")


class CloudflareProvisioningUncertain(CloudflareProvisioningError):
    """A mutation may have reached Cloudflare, so a later job must read back."""


Checkpoint = Callable[[str, dict[str, object]], Awaitable[None]]


class CloudflareDeploymentAdapter:
    """Reconcile one capacity Worker through Cloudflare's documented HTTP APIs.

    The injected client remains caller-owned. It allows the job runner to own
    connection lifetime, observability, and encrypted credential resolution.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        artifact_directory: Path,
        *,
        checkpoint: Checkpoint | None = None,
    ) -> None:
        self._http = http
        self._artifact_directory = artifact_directory
        self._checkpoint = checkpoint

    async def list_accounts(self, api_token: CloudflareApiToken) -> list[dict[str, object]]:
        """Discover accounts through the same scoped API-token auth as deployment."""
        headers = {"Authorization": f"Bearer {api_token.value}"}
        accounts: list[dict[str, object]] = []
        page = 1
        while True:
            response = await self._request(
                "GET", CF_API_ORIGIN + CF_V4_PATH + "/accounts",
                headers=headers, params={"per_page": "50", **({"page": str(page)} if page > 1 else {})},
                step="account_discovery", mutation=False,
            )
            result = self._result(response, "account_discovery")
            if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
                raise CloudflareProvisioningError("account_discovery")
            accounts.extend(result)
            if len(accounts) > 1000:
                raise CloudflareProvisioningError("account_discovery_narrow_token")
            body = self._json(response, "account_discovery")
            result_info = body.get("result_info")
            total_pages = result_info.get("total_pages") if isinstance(result_info, dict) else None
            if total_pages is None:
                return accounts
            if not isinstance(total_pages, int) or not 1 <= total_pages <= 20:
                raise CloudflareProvisioningError("account_discovery_narrow_token")
            if page >= total_pages:
                return accounts
            page += 1

    async def reconcile(
        self,
        deployment: CloudflareDeployment,
        *,
        api_token: CloudflareApiToken,
        capacity_token: CapacityToken,
    ) -> CloudflareDeploymentResult:
        """Provision or reconcile the Worker, its Container app, and its route."""
        self._validate_deployment(deployment)
        headers = {"Authorization": f"Bearer {api_token.value}"}
        service = await self._request(
            "GET", self._url(deployment, f"/accounts/{deployment.account_id}/workers/services/{deployment.script_name}"),
            headers=headers, step="migration_readback", mutation=False, allow_statuses={404},
        )
        include_migration = self._needs_initial_migration(service)

        await self._upload_worker(deployment, headers, include_migration)
        await self._checkpoint_step("worker_uploaded", {"migration_tag": MIGRATION_TAG})

        namespace_id = await self._container_namespace(deployment, headers)
        application_id, created = await self._reconcile_application(deployment, headers, namespace_id)
        await self._checkpoint_step(
            "container_application_created" if created else "container_application_reconciled",
            {"application_id": application_id, "container_namespace_id": namespace_id},
        )

        await self._mutate_json(
            "PUT", deployment, headers,
            f"/accounts/{deployment.account_id}/workers/scripts/{deployment.script_name}/secrets",
            {"name": "CAPACITY_TOKEN", "text": capacity_token.value, "type": "secret_text"},
            step="capacity_secret_upsert",
        )
        await self._checkpoint_step("capacity_secret_upserted", {})

        await self._mutate_json(
            "POST", deployment, headers,
            f"/accounts/{deployment.account_id}/workers/scripts/{deployment.script_name}/subdomain",
            {"enabled": True, "previews_enabled": False}, step="worker_route_enable",
        )
        subdomain_response = await self._request(
            "GET", self._url(deployment, f"/accounts/{deployment.account_id}/workers/subdomain"),
            headers=headers, step="worker_subdomain_readback", mutation=False,
        )
        subdomain_result = self._result(subdomain_response, "worker_subdomain_readback")
        subdomain = subdomain_result.get("subdomain") if isinstance(subdomain_result, dict) else None
        if not isinstance(subdomain, str) or not subdomain:
            raise CloudflareProvisioningError("worker_subdomain_readback")
        await self._checkpoint_step("worker_route_enabled", {"subdomain": subdomain})

        return CloudflareDeploymentResult(
            application_id=application_id,
            container_namespace_id=namespace_id,
            migration_tag=MIGRATION_TAG,
            worker_url=f"https://{deployment.script_name}.{subdomain}.workers.dev",
        )

    async def _upload_worker(
        self, deployment: CloudflareDeployment, headers: dict[str, str], include_migration: bool,
    ) -> None:
        modules = self._artifact_modules()
        metadata: dict[str, object] = {
            "main_module": "capacity.mjs",
            "compatibility_date": CLOUDFLARE_COMPATIBILITY_DATE,
            "observability": {"enabled": True},
            "bindings": self._worker_bindings(deployment),
            "containers": [{"name": deployment.container_application_name, "class_name": CONTAINER_CLASS}],
            "keep_bindings": ["secret_text"],
        }
        if include_migration:
            metadata["migrations"] = {
                "new_tag": MIGRATION_TAG,
                "steps": [{"new_sqlite_classes": list(SQLITE_DURABLE_OBJECT_CLASSES)}],
            }
        files: list[tuple[str, tuple[str | None, bytes | str, str]]] = [
            ("metadata", (None, json.dumps(metadata, separators=(",", ":")), "application/json")),
            *[(name, (name, content, "application/javascript+module")) for name, content in modules.items()],
        ]
        response = await self._request(
            "PUT", self._url(deployment, f"/accounts/{deployment.account_id}/workers/scripts/{deployment.script_name}"),
            headers=headers, files=files, step="worker_upload", mutation=True,
        )
        self._result(response, "worker_upload")

    async def _container_namespace(self, deployment: CloudflareDeployment, headers: dict[str, str]) -> str:
        response = await self._request(
            "GET", self._url(deployment, f"/accounts/{deployment.account_id}/workers/durable_objects/namespaces"),
            headers=headers, params={"per_page": "1000"}, step="durable_object_namespace_readback", mutation=False,
        )
        result = self._result(response, "durable_object_namespace_readback")
        if not isinstance(result, list):
            raise CloudflareProvisioningError("durable_object_namespace_readback")
        matches = [
            item for item in result
            if isinstance(item, dict)
            and item.get("class") == CONTAINER_CLASS
            and item.get("script") == deployment.script_name
            and item.get("preview") is None
            and isinstance(item.get("id"), str)
        ]
        if len(matches) != 1:
            raise CloudflareProvisioningError("durable_object_namespace_readback")
        return str(matches[0]["id"])

    async def _reconcile_application(
        self, deployment: CloudflareDeployment, headers: dict[str, str], namespace_id: str,
    ) -> tuple[str, bool]:
        applications = await self._list_applications(deployment, headers)
        matching = [app for app in applications if app.get("name") == deployment.container_application_name]
        if len(matching) > 1:
            raise CloudflareProvisioningError("container_application_readback")
        desired = self._application_body(deployment, namespace_id)
        if not matching:
            response = await self._request(
                "POST", self._url(deployment, f"/accounts/{deployment.account_id}/containers/applications"),
                headers=headers, json=desired, step="container_application_create", mutation=True,
            )
            result = self._result(response, "container_application_create")
            application_id = result.get("id") if isinstance(result, dict) else None
            if not isinstance(application_id, str) or not application_id:
                raise CloudflareProvisioningError("container_application_create")
            return application_id, True

        existing = matching[0]
        application_id = existing.get("id")
        current_namespace = existing.get("durable_objects", {}).get("namespace_id") if isinstance(
            existing.get("durable_objects"), dict,
        ) else None
        if not isinstance(application_id, str) or current_namespace != namespace_id:
            raise CloudflareProvisioningError("container_application_different durable object namespace")
        patch = self._application_patch(existing, desired)
        if patch:
            response = await self._request(
                "PATCH", self._url(deployment, f"/accounts/{deployment.account_id}/containers/applications/{application_id}"),
                headers=headers, json=patch, step="container_application_patch", mutation=True,
            )
            self._result(response, "container_application_patch")
        return application_id, False

    async def _list_applications(
        self, deployment: CloudflareDeployment, headers: dict[str, str],
    ) -> list[dict[str, object]]:
        applications: list[dict[str, object]] = []
        params: dict[str, str] = {"per_page": "500"}
        while True:
            response = await self._request(
                "GET", self._url(deployment, f"/accounts/{deployment.account_id}/containers/applications"),
                headers=headers, params=params, step="container_application_readback", mutation=False,
            )
            result = self._result(response, "container_application_readback")
            if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
                raise CloudflareProvisioningError("container_application_readback")
            applications.extend(result)
            body = self._json(response, "container_application_readback")
            result_info = body.get("result_info")
            next_page = result_info.get("next_page_token") if isinstance(result_info, dict) else None
            if next_page is None:
                return applications
            if not isinstance(next_page, str) or not next_page:
                raise CloudflareProvisioningError("container_application_readback")
            params = {"per_page": "500", "page_token": next_page}

    def _needs_initial_migration(self, response: httpx.Response) -> bool:
        if response.status_code == 404:
            return True
        result = self._result(response, "migration_readback")
        try:
            migration_tag = result["default_environment"]["script"].get("migration_tag")
        except (KeyError, TypeError):
            raise CloudflareProvisioningError("migration_readback") from None
        if migration_tag == MIGRATION_TAG:
            return False
        raise CloudflareProvisioningError("migration_readback_migration")

    async def _mutate_json(
        self, method: str, deployment: CloudflareDeployment, headers: dict[str, str], path: str,
        payload: dict[str, object], *, step: str,
    ) -> None:
        response = await self._request(
            method, self._url(deployment, path), headers=headers, json=payload, step=step, mutation=True,
        )
        self._result(response, step)

    async def _checkpoint_step(self, step: str, detail: dict[str, object]) -> None:
        """Let the canonical PlatformJob persist a public reconciliation checkpoint."""
        if self._checkpoint is not None:
            await self._checkpoint(step, detail)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        step: str,
        mutation: bool,
        allow_statuses: set[int] | None = None,
        **kwargs: object,
    ) -> httpx.Response:
        try:
            response = await self._http.request(method, url, headers=headers, **kwargs)
        except httpx.RequestError as exc:
            error_type = CloudflareProvisioningUncertain if mutation else CloudflareProvisioningError
            raise error_type(step) from exc
        if response.status_code in (allow_statuses or set()):
            return response
        if not 200 <= response.status_code < 300:
            raise CloudflareProvisioningError(step, status_code=response.status_code)
        return response

    def _result(self, response: httpx.Response, step: str) -> Any:
        body = self._json(response, step)
        if body.get("success") is not True:
            raise CloudflareProvisioningError(step, status_code=response.status_code)
        return body.get("result")

    @staticmethod
    def _json(response: httpx.Response, step: str) -> dict[str, Any]:
        try:
            body = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise CloudflareProvisioningError(step, status_code=response.status_code) from None
        if not isinstance(body, dict):
            raise CloudflareProvisioningError(step, status_code=response.status_code)
        return body

    def _artifact_modules(self) -> dict[str, bytes]:
        modules: dict[str, bytes] = {}
        for name in ("capacity.mjs", "capacity-core.mjs"):
            path = self._artifact_directory / name
            try:
                modules[name] = path.read_bytes()
            except OSError:
                raise CloudflareProvisioningError("worker_artifact") from None
        return modules

    @staticmethod
    def _worker_bindings(deployment: CloudflareDeployment) -> list[dict[str, object]]:
        limits = deployment.limits
        text_vars: dict[str, str] = {
            "SCOPE": deployment.scope,
            "WORKER_VERSION": deployment.worker_version,
            "MEMORY_LIMIT_BYTES": str(limits.memory_limit_bytes),
            "MAX_WORKERS": str(limits.max_workers),
            "MAX_LIFETIME_SECONDS": str(limits.max_lifetime_seconds),
            "STARTING_GRACE_MS": str(limits.starting_grace_ms),
            "EXECUTION_HANDLERS": json.dumps(limits.execution_handlers, separators=(",", ":")),
            "COMPUTE_CAPABILITIES": json.dumps(limits.compute_capabilities, separators=(",", ":")),
            "WORKER_CONTAINER_ENV": json.dumps({
                "BIFROST_API_URL": deployment.bifrost_api_origin,
                "HOME": "/tmp/bifrost",
                "PYTHONUSERBASE": "/tmp/bifrost/.local",
            }, separators=(",", ":"), sort_keys=True),
        }
        return [
            *[
                {"name": name, "type": "durable_object_namespace", "class_name": class_name}
                for name, class_name in (
                    ("WORKER_LEDGER", "WorkerLedger"),
                    ("SCOPE_REGISTRY", "ScopeRegistry"),
                    ("WORKER_CONTAINER", CONTAINER_CLASS),
                )
            ],
            *[{"name": name, "type": "plain_text", "text": value} for name, value in text_vars.items()],
        ]

    @staticmethod
    def _application_body(deployment: CloudflareDeployment, namespace_id: str) -> dict[str, object]:
        return {
            "name": deployment.container_application_name,
            "scheduling_policy": "default",
            "configuration": {"image": deployment.registry_image, "instance_type": CONTAINER_INSTANCE_TYPE},
            "instances": 0,
            "max_instances": deployment.limits.max_workers,
            "durable_objects": {"namespace_id": namespace_id},
        }

    @staticmethod
    def _application_patch(existing: dict[str, object], desired: dict[str, object]) -> dict[str, object]:
        patch = {
            key: desired[key]
            for key in ("scheduling_policy", "configuration", "max_instances")
            if existing.get(key) != desired[key]
        }
        return patch

    @staticmethod
    def _url(deployment: CloudflareDeployment, path: str) -> str:
        return CF_API_ORIGIN + CF_V4_PATH + path

    @staticmethod
    def _validate_deployment(deployment: CloudflareDeployment) -> None:
        limits = deployment.limits
        if not all((
            deployment.account_id, deployment.script_name, deployment.scope,
            deployment.bifrost_api_origin, deployment.worker_version,
        )):
            raise CloudflareProvisioningError("deployment_validation")
        if not deployment.registry_image.startswith("registry.cloudflare.com/") or "@sha256:" not in deployment.registry_image:
            raise CloudflareProvisioningError("deployment_validation")
        if not (1 <= limits.max_workers <= MAX_CONFIGURED_WORKERS):
            raise CloudflareProvisioningError("deployment_validation")
        if limits.memory_limit_bytes != QUALIFIED_MEMORY_LIMIT_BYTES:
            raise CloudflareProvisioningError("deployment_validation")
        if not 60 <= limits.max_lifetime_seconds <= 6 * 3600 or not 0 <= limits.starting_grace_ms <= 600_000:
            raise CloudflareProvisioningError("deployment_validation")
