"""Real HTTP/DB/worker contracts against the isolated TLS GitHub App fixture."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from scripts.github_app_fixture import APP_ID, KEY_PATH
from src.models.orm.events import Event, EventDelivery, EventSource, WebhookSource
from src.models.orm.github_app import GitHubAppDelivery, GitHubAppInstallation, GitHubAppRegistration
from tests.e2e.conftest import poll_until, write_and_register

pytestmark = pytest.mark.e2e
SECRET = "github-app-e2e-webhook-secret-32-characters"


@pytest.fixture(scope="module")
def app_admin(e2e_client, platform_admin, second_platform_admin):
    """One explicit temporary grant for this module's shared App/job journey."""
    url = f"/api/users/{platform_admin.user_id}/role-assignments"
    response = e2e_client.get(url, headers=second_platform_admin.headers)
    assert response.status_code == 200, response.text
    original = response.json()
    additional = [{"role_id": item["role_id"], "boundaries": [
        {"kind": boundary["kind"], "organization_id": boundary["organization_id"]} for boundary in item["boundaries"]
    ]} for item in original["additional"]]
    grant = e2e_client.put(url, headers=second_platform_admin.headers, json={
        "base_role_id": original["base_role"]["id"],
        "additional": [*additional, {"role_id": "00000000-0000-0000-0000-000000000008"}]})
    assert grant.status_code == 200, grant.text
    try:
        yield platform_admin
    finally:
        restored = e2e_client.put(url, headers=second_platform_admin.headers, json={
            "base_role_id": original["base_role"]["id"], "additional": additional})
        assert restored.status_code == 200, restored.text


@pytest.fixture(scope="module")
def app_connection(e2e_client, app_admin, org1, org2, async_session_factory):
    headers = app_admin.headers
    pem = KEY_PATH.read_text()
    configured = e2e_client.put("/api/github-app/config", headers=headers,
        json={"app_id": APP_ID, "private_key": pem, "webhook_secret": SECRET})
    assert configured.status_code == 200, configured.text
    assert configured.json()["configured"] is True
    assert pem not in configured.text and SECRET not in configured.text
    bindings = []
    for installation_id, organization in [(70001, org1), (70002, org2)]:
        response = e2e_client.post("/api/github-app/installations", headers=headers,
            json={"installation_id": installation_id, "organization_id": organization["id"]})
        assert response.status_code == 201, response.text
        bindings.append(response.json())
    recovery = e2e_client.post("/api/github-app/reconcile", headers=headers)
    assert recovery.status_code == 202, recovery.text
    job_id = recovery.json()["job_id"]
    terminal = wait_job(e2e_client, headers, job_id)
    assert terminal["status"] == "succeeded", terminal
    try:
        yield {"headers": headers, "bindings": bindings, "pem": pem}
    finally:
        e2e_client.delete("/api/github-app/config", headers=headers)

        async def cleanup():
            async with async_session_factory() as db:
                await db.execute(delete(EventSource).where(EventSource.id.in_([UUID(binding["event_source_id"]) for binding in bindings])))
                await db.execute(delete(GitHubAppDelivery).where(GitHubAppDelivery.app_id == APP_ID))
                await db.execute(delete(GitHubAppRegistration).where(GitHubAppRegistration.id == 1))
                await db.commit()
        asyncio.run(cleanup())


def payload():
    return {"action": "opened", "installation": {"id": 70001},
            "repository": {"id": 80001}, "issue": {"number": 10, "title": "Synthetic issue"}}


def wait_job(e2e_client, headers, job_id):
    def terminal():
        job = e2e_client.get(f"/api/platform-jobs/{job_id}", headers=headers).json()
        return job if job["status"] in {"succeeded", "failed"} else None
    return poll_until(terminal, max_wait=90, interval=0.2)


def send(e2e_client, value=None, *, delivery_id=None, app_id=APP_ID, signature=True, event="issues"):
    body = json.dumps(payload() if value is None else value).encode()
    headers = {"X-GitHub-Delivery": delivery_id or str(uuid4()), "X-GitHub-Event": event, "Content-Type": "application/json"}
    if signature:
        headers["X-Hub-Signature-256"] = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return e2e_client.post(f"/api/hooks/github-app/{app_id}", content=body, headers=headers)


def test_configuration_inventory_and_authorization_boundaries(e2e_client, app_connection, org1, org2_user):
    headers = app_connection["headers"]
    read = e2e_client.get("/api/github-app/config", headers=headers)
    assert read.status_code == 200
    assert app_connection["pem"] not in read.text and SECRET not in read.text
    inventory = e2e_client.get("/api/github-app/installations", params={"organization_id": org1["id"]}, headers=headers)
    assert inventory.status_code == 200, inventory.text
    installations = inventory.json()["installations"]
    assert [row["installation_id"] for row in installations] == [70001]
    assert installations[0]["status"] == "active"
    assert installations[0]["repositories"] == [{"id": 80001, "full_name": "fixture/repository-70001", "private": True}]
    assert "fixture-installation-" not in inventory.text
    source = e2e_client.get(f"/api/events/sources/{installations[0]['event_source_id']}", headers=headers)
    assert source.status_code == 200, source.text
    assert source.json()["webhook"]["callback_url"] == f"/api/hooks/github-app/{APP_ID}"
    assert source.json()["webhook"]["secret_set"] is True
    denied = e2e_client.get("/api/github-app/installations", params={"organization_id": org1["id"]}, headers=org2_user.headers)
    assert denied.status_code == 403
    assert e2e_client.get("/api/github-app/config", headers=org2_user.headers).status_code == 403
    conflict = e2e_client.post("/api/github-app/installations", headers=headers,
        json={"installation_id": 70001, "organization_id": str(org2_user.organization_id)})
    assert conflict.status_code == 409
    unbound = e2e_client.post("/api/github-app/installations", headers=headers,
        json={"installation_id": 99999, "organization_id": org1["id"]})
    assert unbound.status_code == 409


def test_app_state_is_not_exported_or_overwritten_by_workspace_sync(app_connection, async_session_factory):
    from bifrost.manifest import ManifestEventSource
    from fastapi import HTTPException
    from src.services.manifest_generator import generate_manifest
    from src.services.manifest_import import ManifestResolver

    async def check():
        async with async_session_factory() as db:
            manifest = await generate_manifest(db)
            exported = manifest.model_dump_json()
            assert SECRET not in exported and app_connection["pem"] not in exported
            assert all(binding["event_source_id"] not in manifest.events for binding in app_connection["bindings"])
            forged = ManifestEventSource(id=app_connection["bindings"][0]["event_source_id"],
                source_type="webhook", adapter_name="generic", organization_id=None)
            with pytest.raises(HTTPException) as error:
                await ManifestResolver(db)._resolve_event_source("Forged source", forged)
            assert error.value.status_code == 409
            binding = await db.get(GitHubAppInstallation, UUID(app_connection["bindings"][0]["id"]))
            source = await db.get(EventSource, binding.event_source_id)
            assert source.organization_id == binding.organization_id
    asyncio.run(check())


@pytest.mark.parametrize("case", ["missing_signature", "wrong_route", "wrong_repository", "unknown_installation"])
def test_rejected_requests_do_not_create_events(e2e_client, app_connection, async_session_factory, case):
    identity = str(uuid4())
    value = payload()
    if case == "wrong_repository":
        value["repository"]["id"] = 80002
    if case == "unknown_installation":
        value["installation"]["id"] = 99999
    response = send(e2e_client, value, delivery_id=identity,
        signature=case != "missing_signature", app_id=APP_ID + 1 if case == "wrong_route" else APP_ID)
    assert response.status_code in {401, 403, 404}, response.text

    async def check():
        async with async_session_factory() as db:
            assert await db.get(GitHubAppDelivery, (APP_ID, identity)) is None
    asyncio.run(check())


def test_source_rate_limit_rejects_new_events_but_allows_duplicate_receipts(e2e_client, app_connection):
    binding = app_connection["bindings"][1]

    def limit(value):
        response = e2e_client.patch(f"/api/events/sources/{binding['event_source_id']}", headers=app_connection["headers"],
            json={"organization_id": binding["organization_id"], "webhook": {"config": {}, "rate_limit_per_minute": value}})
        assert response.status_code == 200, response.text

    identity = str(uuid4())
    value = {**payload(), "installation": {"id": 70002}, "repository": {"id": 80002}}
    limit(1)
    try:
        assert send(e2e_client, value, delivery_id=identity).status_code == 202
        limited = send(e2e_client, value)
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) > 0
        assert send(e2e_client, value, delivery_id=identity).status_code == 202
    finally:
        limit(60)


def test_concurrent_duplicates_fan_out_once_and_reuse_committed_identity(e2e_client, app_connection, org1, async_session_factory):
    headers = app_connection["headers"]
    name = "github_app_echo_" + uuid4().hex[:8]
    path = f"{name}.py"
    names = [name + "_first", name + "_second"]
    content = "from bifrost import workflow\n" + "\n".join(
        f'@workflow(name="{target}")\nasync def {target}(event: dict) -> dict:\n    return {{"delivery_id": event["delivery_id"], "repository_id": event["repository_id"]}}\n'
        for target in names)
    workflows = [write_and_register(e2e_client, headers, path, content, target, organization_id=org1["id"]) for target in names]
    subscription_ids = []
    try:
        for workflow in workflows:
            subscription = e2e_client.post(f"/api/events/sources/{app_connection['bindings'][0]['event_source_id']}/subscriptions", headers=headers, json={
                "workflow_id": workflow["id"], "event_type": "github.issues.opened", "input_mapping": {"event": "{{ payload }}"}})
            assert subscription.status_code == 201, subscription.text
            subscription_ids.append(subscription.json()["id"])
        identity = str(uuid4())
        with ThreadPoolExecutor(max_workers=6) as pool:
            responses = list(pool.map(lambda _: send(e2e_client, delivery_id=identity), range(6)))
        assert [response.status_code for response in responses] == [202] * 6

        async def observe():
            async with async_session_factory() as db:
                receipt = await db.get(GitHubAppDelivery, (APP_ID, identity))
                assert receipt and receipt.event_id
                event = await db.get(Event, receipt.event_id)
                assert event.organization_id == UUID(org1["id"])
                deliveries = list((await db.execute(select(EventDelivery).where(EventDelivery.event_id == event.id))).scalars())
                assert len(deliveries) == 2
                assert len({delivery.execution_id for delivery in deliveries}) == 2
                return sorted(str(delivery.execution_id) for delivery in deliveries)

        executions = asyncio.run(observe())
        for execution_id in executions:
            def terminal():
                execution = e2e_client.get(f"/api/executions/{execution_id}", headers=headers).json()
                return execution if execution.get("status") in {"Success", "Failed", "Timeout"} else None
            execution = poll_until(terminal, max_wait=60, interval=0.2)
            assert execution["status"] == "Success", execution
        # Provider redelivery is the same identity, even after workflow completion.
        assert send(e2e_client, delivery_id=identity).status_code == 202
        assert asyncio.run(observe()) == executions
        conflicting = payload()
        conflicting["issue"]["title"] = "Changed bytes"
        assert send(e2e_client, conflicting, delivery_id=identity).status_code == 409

        # A second event proves the distinct commit-before-wakeup crash seam.
        # Four total workflow jobs: two-target fan-out, then two after lost wakeup.
        prior_executions = executions
        identity = str(uuid4())

        async def commit_without_handoff():
            from shared.github_app_events import accept_webhook

            body = json.dumps(payload()).encode()
            signed = {"x-github-delivery": identity, "x-github-event": "issues",
                "x-hub-signature-256": "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()}
            async with async_session_factory() as db:
                queued = await accept_webhook(db, APP_ID, body, signed)
                assert queued.count == 2
                await db.commit()
                # Intentionally discard hand_off: durable shared dispatch must
                # survive the accepting process disappearing after its commit.

        asyncio.run(commit_without_handoff())
        executions = asyncio.run(observe())
        assert not set(executions) & set(prior_executions)
        for execution_id in executions:
            def terminal_after_crash():
                execution = e2e_client.get(f"/api/executions/{execution_id}", headers=headers).json()
                return execution if execution.get("status") in {"Success", "Failed", "Timeout"} else None
            execution = poll_until(terminal_after_crash, max_wait=60, interval=0.2)
            assert execution["status"] == "Success", execution
        assert send(e2e_client, delivery_id=identity).status_code == 202
        assert asyncio.run(observe()) == executions
    finally:
        for subscription_id in subscription_ids:
            e2e_client.delete(f"/api/events/subscriptions/{subscription_id}", headers=headers)
        e2e_client.delete(f"/api/files/editor?path={path}", headers=headers)


def test_lifecycle_invalidates_inventory_and_generic_route_cannot_bypass_hmac(e2e_client, app_connection, async_session_factory):
    binding = app_connection["bindings"][0]
    assert e2e_client.post(f"/api/hooks/{binding['event_source_id']}", json=payload()).status_code == 404

    async def set_adapter(name):
        async with async_session_factory() as db:
            source = (await db.execute(select(WebhookSource).where(
                WebhookSource.event_source_id == UUID(binding["event_source_id"])))).scalar_one()
            source.adapter_name = name
            await db.commit()

    asyncio.run(set_adapter("generic"))
    try:
        assert e2e_client.post(f"/api/hooks/{binding['event_source_id']}", json=payload()).status_code == 404
    finally:
        asyncio.run(set_adapter("github_app"))
    lifecycle = {"action": "suspend", "installation": {"id": 70001}}
    assert send(e2e_client, lifecycle, event="installation").status_code == 202
    assert send(e2e_client).status_code == 403
    inventory = e2e_client.get("/api/github-app/installations", headers=app_connection["headers"],
        params={"organization_id": binding["organization_id"]}).json()["installations"]
    assert inventory[0]["status"] == "stale" and inventory[0]["repositories"] == []
    recovery = e2e_client.post("/api/github-app/reconcile", headers=app_connection["headers"])
    assert wait_job(e2e_client, app_connection["headers"], recovery.json()["job_id"])["status"] == "succeeded"
