"""Cloudflare's HTTP-only capacity-provider provisioning contract."""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest

from src.services.worker_pool.cloudflare_deploy import (
    CapacityToken,
    CloudflareApiToken,
    CloudflareDeployment,
    CloudflareDeploymentAdapter,
    CloudflareDeploymentLimits,
    CloudflareProvisioningError,
    CloudflareProvisioningUncertain,
)


TOKEN = "cloudflare-api-token-value"
CAPACITY_TOKEN = "capacity-token-value-that-must-never-be-logged"


@pytest.fixture
def artifact_directory(tmp_path):
    (tmp_path / "capacity.mjs").write_text('import "./capacity-core.mjs";\n', encoding="utf-8")
    (tmp_path / "capacity-core.mjs").write_text("export const core = true;\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def deployment():
    return CloudflareDeployment(
        account_id="account-id",
        script_name="bifrost-capacity",
        scope="capacity-scope",
        bifrost_api_origin="https://bifrost.example.test",
        worker_version="worker-v1",
        registry_image="registry.cloudflare.com/bifrost/worker@sha256:" + "a" * 64,
        limits=CloudflareDeploymentLimits(
            memory_limit_bytes=4096 * 2**20,
            max_workers=2,
            max_lifetime_seconds=900,
            starting_grace_ms=30_000,
            execution_handlers=("workflow.execute",),
            compute_capabilities=("app.compile.v2",),
        ),
    )


def _ok(result: object) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "errors": [], "result": result})


def _not_found() -> httpx.Response:
    return httpx.Response(404, json={"success": False, "errors": [{"code": 10007, "message": "not found"}]})


def _namespaces() -> list[dict[str, object]]:
    return [
        {"id": "ledger-ns", "class": "WorkerLedger", "script": "bifrost-capacity", "preview": None},
        {"id": "scope-ns", "class": "ScopeRegistry", "script": "bifrost-capacity", "preview": None},
        {"id": "container-ns", "class": "WorkerContainer", "script": "bifrost-capacity", "preview": None},
    ]


def _response_for_complete_reconcile(
    request: httpx.Request, *, migration_tag: str | None = None, max_workers: int = 2,
) -> httpx.Response:
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    path = request.url.path
    if path == "/client/v4/accounts/account-id/workers/services/bifrost-capacity":
        if migration_tag is None:
            return _not_found()
        return _ok({"default_environment": {"script": {"migration_tag": migration_tag}}})
    if path == "/client/v4/accounts/account-id/workers/scripts/bifrost-capacity":
        assert request.method == "PUT"
        assert request.headers["content-type"].startswith("multipart/form-data;")
        body = request.content.decode()
        assert '"main_module":"capacity.mjs"' in body
        assert '"name":"WORKER_LEDGER"' in body
        assert '"type":"durable_object_namespace"' in body
        assert '"name":"WORKER_CONTAINER"' in body
        assert '"containers":[{"name":"bifrost-capacity-worker-container","class_name":"WorkerContainer"}]' in body
        assert '"compatibility_date":"2026-11-10"' in body
        assert '"observability":{"enabled":true}' in body
        assert '"keep_bindings":["secret_text"]' in body
        assert '"type":"plain_text"' in body
        assert '"name":"SCOPE"' in body
        assert '"text":"capacity-scope"' in body
        assert '"name":"MAX_WORKERS"' in body
        assert f'"text":"{max_workers}"' in body
        assert '"name":"WORKER_CONTAINER_ENV"' in body
        assert "BIFROST_API_URL" in body
        assert "https://bifrost.example.test" in body
        assert '"name":"CAPACITY_TOKEN"' not in body
        if migration_tag is None:
            assert '"migrations"' in body
            assert '"new_sqlite_classes"' in body
        else:
            assert '"migrations"' not in body
        return _ok({})
    if path == "/client/v4/accounts/account-id/workers/durable_objects/namespaces":
        assert request.method == "GET"
        assert dict(request.url.params) == {"per_page": "1000"}
        return _ok(_namespaces())
    if path == "/client/v4/accounts/account-id/containers/applications":
        if request.method == "GET":
            assert dict(request.url.params) == {"per_page": "500"}
            return _ok([])
        assert request.method == "POST"
        assert json.loads(request.content) == {
            "name": "bifrost-capacity-worker-container",
            "scheduling_policy": "default",
            "configuration": {
                "image": "registry.cloudflare.com/bifrost/worker@sha256:" + "a" * 64,
                "instance_type": "standard-1",
            },
            "instances": 0,
            "max_instances": max_workers,
            "durable_objects": {"namespace_id": "container-ns"},
        }
        return _ok({"id": "application-id"})
    if path == "/client/v4/accounts/account-id/workers/scripts/bifrost-capacity/secrets":
        assert request.method == "PUT"
        assert json.loads(request.content) == {"name": "CAPACITY_TOKEN", "text": CAPACITY_TOKEN, "type": "secret_text"}
        return _ok({})
    if path == "/client/v4/accounts/account-id/workers/scripts/bifrost-capacity/subdomain":
        assert request.method == "POST"
        assert json.loads(request.content) == {"enabled": True, "previews_enabled": False}
        return _ok({})
    if path == "/client/v4/accounts/account-id/workers/subdomain":
        assert request.method == "GET"
        return _ok({"subdomain": "account-subdomain"})
    pytest.fail(f"unexpected Cloudflare request: {request.method} {request.url}")


@pytest.mark.asyncio
async def test_reconcile_uploads_worker_then_owns_container_resources_over_cloudflare_http(
    artifact_directory, deployment,
) -> None:
    steps: list[str] = []

    async def checkpoint(step: str, detail: dict[str, object]) -> None:
        steps.append(step)
        assert CAPACITY_TOKEN not in repr(detail)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(_response_for_complete_reconcile),
    ) as http:
        result = await CloudflareDeploymentAdapter(http, artifact_directory, checkpoint=checkpoint).reconcile(
            deployment, api_token=CloudflareApiToken(TOKEN), capacity_token=CapacityToken(CAPACITY_TOKEN),
        )

    assert result.application_id == "application-id"
    assert result.container_namespace_id == "container-ns"
    assert result.worker_url == "https://bifrost-capacity.account-subdomain.workers.dev"
    assert result.migration_tag == "v1"
    assert steps == ["worker_uploaded", "container_application_created", "capacity_secret_upserted", "worker_route_enabled"]


@pytest.mark.asyncio
async def test_reconcile_does_not_repeat_an_already_applied_migration(artifact_directory, deployment) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _response_for_complete_reconcile(request, migration_tag="v1")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await CloudflareDeploymentAdapter(http, artifact_directory).reconcile(
            deployment, api_token=CloudflareApiToken(TOKEN), capacity_token=CapacityToken(CAPACITY_TOKEN),
        )


@pytest.mark.asyncio
async def test_reconcile_uses_the_qualified_deployment_worker_limit_for_container_instances(
    artifact_directory, deployment,
) -> None:
    one_worker = replace(deployment, limits=replace(deployment.limits, max_workers=1))

    def handler(request: httpx.Request) -> httpx.Response:
        return _response_for_complete_reconcile(request, migration_tag="v1", max_workers=1)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await CloudflareDeploymentAdapter(http, artifact_directory).reconcile(
            one_worker, api_token=CloudflareApiToken(TOKEN), capacity_token=CapacityToken(CAPACITY_TOKEN),
        )


@pytest.mark.asyncio
async def test_reconcile_refuses_an_ambiguous_migration_before_mutating(artifact_directory, deployment) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _ok({"default_environment": {"script": {"migration_tag": "v2"}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(CloudflareProvisioningError, match="migration"):
            await CloudflareDeploymentAdapter(http, artifact_directory).reconcile(
                deployment, api_token=CloudflareApiToken(TOKEN), capacity_token=CapacityToken(CAPACITY_TOKEN),
            )
    assert [(request.method, request.url.path) for request in requests] == [
        ("GET", "/client/v4/accounts/account-id/workers/services/bifrost-capacity"),
    ]


@pytest.mark.asyncio
async def test_reconcile_fails_closed_when_existing_application_has_another_namespace(artifact_directory, deployment) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/workers/services/bifrost-capacity"):
            return _ok({"default_environment": {"script": {"migration_tag": "v1"}}})
        if path.endswith("/workers/scripts/bifrost-capacity"):
            return _ok({})
        if path.endswith("/workers/durable_objects/namespaces"):
            return _ok(_namespaces())
        if path.endswith("/containers/applications"):
            assert request.method == "GET"
            return _ok([{
                "id": "application-id", "name": "bifrost-capacity-worker-container",
                "durable_objects": {"namespace_id": "foreign-namespace"},
            }])
        pytest.fail(f"unexpected Cloudflare request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(CloudflareProvisioningError, match="different durable object namespace"):
            await CloudflareDeploymentAdapter(http, artifact_directory).reconcile(
                deployment, api_token=CloudflareApiToken(TOKEN), capacity_token=CapacityToken(CAPACITY_TOKEN),
            )


@pytest.mark.asyncio
async def test_reconcile_rolls_an_existing_matched_container_application_to_the_pinned_image(
    artifact_directory, deployment,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/workers/services/bifrost-capacity"):
            return _ok({"default_environment": {"script": {"migration_tag": "v1"}}})
        if path.endswith("/workers/scripts/bifrost-capacity"):
            return _ok({})
        if path.endswith("/workers/durable_objects/namespaces"):
            return _ok(_namespaces())
        if path.endswith("/containers/applications"):
            assert request.method == "GET"
            return _ok([{
                "id": "application-id", "name": "bifrost-capacity-worker-container",
                "scheduling_policy": "default", "max_instances": 2,
                "configuration": {"image": "registry.cloudflare.com/bifrost/worker@sha256:old", "instance_type": "standard-1"},
                "durable_objects": {"namespace_id": "container-ns"},
            }])
        if path.endswith("/containers/applications/application-id"):
            assert request.method == "PATCH"
            assert json.loads(request.content) == {
                "configuration": {
                    "image": "registry.cloudflare.com/bifrost/worker@sha256:" + "a" * 64,
                    "instance_type": "standard-1",
                },
            }
            return _ok({"id": "application-id"})
        if path.endswith("/workers/scripts/bifrost-capacity/secrets"):
            return _ok({})
        if path.endswith("/workers/scripts/bifrost-capacity/subdomain"):
            return _ok({})
        if path.endswith("/workers/subdomain"):
            return _ok({"subdomain": "account-subdomain"})
        pytest.fail(f"unexpected Cloudflare request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await CloudflareDeploymentAdapter(http, artifact_directory).reconcile(
            deployment, api_token=CloudflareApiToken(TOKEN), capacity_token=CapacityToken(CAPACITY_TOKEN),
        )
    assert result.application_id == "application-id"


@pytest.mark.asyncio
async def test_lost_mutation_response_is_reported_uncertain_without_a_retry(artifact_directory, deployment) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if request.method == "GET":
            return _not_found()
        raise httpx.ReadError("connection lost after upstream accepted request", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(CloudflareProvisioningUncertain) as caught:
            await CloudflareDeploymentAdapter(http, artifact_directory).reconcile(
                deployment, api_token=CloudflareApiToken(TOKEN), capacity_token=CapacityToken(CAPACITY_TOKEN),
            )
    assert caught.value.step == "worker_upload"
    assert calls == 2
    assert TOKEN not in str(caught.value)
    assert CAPACITY_TOKEN not in str(caught.value)


@pytest.mark.asyncio
async def test_provider_errors_and_tokens_are_redacted(artifact_directory, deployment) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return _not_found()
        return httpx.Response(400, json={
            "success": False,
            "errors": [{"code": 10021, "message": f"rejected {CAPACITY_TOKEN}"}],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(CloudflareProvisioningError) as caught:
            await CloudflareDeploymentAdapter(http, artifact_directory).reconcile(
                deployment, api_token=CloudflareApiToken(TOKEN), capacity_token=CapacityToken(CAPACITY_TOKEN),
            )
    assert caught.value.status_code == 400
    assert "worker_upload" in str(caught.value)
    assert CAPACITY_TOKEN not in str(caught.value)
    assert TOKEN not in str(caught.value)
    assert CAPACITY_TOKEN not in repr(CapacityToken(CAPACITY_TOKEN))


@pytest.mark.asyncio
async def test_list_accounts_uses_the_scoped_api_token(artifact_directory) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/client/v4/accounts"
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert "x-auth-key" not in request.headers
        return _ok([{"id": "account-id", "name": "Example"}])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        adapter = CloudflareDeploymentAdapter(http, artifact_directory)
        assert await adapter.list_accounts(CloudflareApiToken(TOKEN)) == [
            {"id": "account-id", "name": "Example"},
        ]


@pytest.mark.asyncio
async def test_list_accounts_reads_all_bounded_cloudflare_pages(artifact_directory) -> None:
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        if "page" not in request.url.params:
            return httpx.Response(200, json={
                "success": True, "errors": [], "result": [{"id": "first", "name": "First"}],
                "result_info": {"page": 1, "per_page": 50, "total_pages": 2},
            })
        assert request.url.params["page"] == "2"
        return httpx.Response(200, json={
            "success": True, "errors": [], "result": [{"id": "second", "name": "Second"}],
            "result_info": {"page": 2, "per_page": 50, "total_pages": 2},
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        accounts = await CloudflareDeploymentAdapter(http, artifact_directory).list_accounts(CloudflareApiToken(TOKEN))
    assert accounts == [{"id": "first", "name": "First"}, {"id": "second", "name": "Second"}]
    assert calls == [{"per_page": "50"}, {"per_page": "50", "page": "2"}]


@pytest.mark.asyncio
async def test_bifrost_callback_origin_never_controls_the_cloudflare_api_host(artifact_directory, deployment) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.cloudflare.com"
        return _response_for_complete_reconcile(request, migration_tag="v1")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await CloudflareDeploymentAdapter(http, artifact_directory).reconcile(
            deployment, api_token=CloudflareApiToken(TOKEN), capacity_token=CapacityToken(CAPACITY_TOKEN),
        )
