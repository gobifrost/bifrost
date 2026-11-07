#!/usr/bin/env python3
"""Populate and verify the representative stable-2 upgrade fixture.

HTTP requests use the standard library in the pinned API images. A read-only
database probe observes the activation pointer omitted from public App metadata.
It writes only compact JSON to stdout: credentials and HTTP response bodies
never appear in diagnostics. The one exception is the legacy access token in
the seed result, which the driver keeps only in memory to prove that target
authentication rejects it.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Any


BASE_URL = "http://localhost:8000"
POLL_SECONDS = 1
JOB_TIMEOUT_SECONDS = 300
EXECUTION_TIMEOUT_SECONDS = 180


class ScenarioError(RuntimeError):
    """A safe error that identifies an assertion without retaining a body."""

    def __init__(self, phase: str, path: str, detail: str, status: int | None = None):
        suffix = f" status={status}" if status is not None else ""
        super().__init__(f"{phase}: {path}:{suffix} {detail}".strip())


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


class Http:
    def __init__(self, phase: str):
        self.phase = phase

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Any | None = None,
        form: dict[str, str] | None = None,
        token: str | None = None,
        multipart: tuple[str, str, bytes] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Response:
        if sum(value is not None for value in (payload, form, multipart)) > 1:
            raise ScenarioError(self.phase, path, "request has conflicting bodies")
        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload, separators=(",", ":")).encode()
        elif form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = urllib.parse.urlencode(form).encode()
        elif multipart is not None:
            field, filename, contents = multipart
            boundary = "----stable2" + hashlib.sha256(contents).hexdigest()[:24]
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            data = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                "Content-Type: application/zip\r\n\r\n"
            ).encode() + contents + f"\r\n--{boundary}--\r\n".encode()
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(BASE_URL + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as result:
                response = Response(result.status, dict(result.headers.items()), result.read())
        except urllib.error.HTTPError as error:
            if error.code in expected:
                return Response(error.code, dict(error.headers.items()), b"")
            raise ScenarioError(self.phase, path, "unexpected HTTP response", error.code) from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise ScenarioError(self.phase, path, f"request failed ({type(error).__name__})") from None
        if response.status not in expected:
            raise ScenarioError(self.phase, path, "unexpected HTTP response", response.status)
        return response

    def json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.request(method, path, **kwargs)
        if not response.body:
            return None
        try:
            return json.loads(response.body)
        except json.JSONDecodeError as error:
            raise ScenarioError(self.phase, path, "response was not JSON", response.status) from error

    def status(self, method: str, path: str, **kwargs: Any) -> int:
        kwargs["expected"] = tuple(range(100, 600))
        return self.request(method, path, **kwargs).status


def require(condition: bool, phase: str, path: str, assertion: str) -> None:
    if not condition:
        raise ScenarioError(phase, path, assertion)


def object_id(data: dict[str, Any], phase: str, path: str) -> str:
    value = data.get("id")
    require(isinstance(value, str) and value, phase, path, "response omitted id")
    return value


def default_password() -> str:
    try:
        return os.environ["BIFROST_DEFAULT_USER_PASSWORD"]
    except KeyError as error:
        raise ScenarioError("setup", "BIFROST_DEFAULT_USER_PASSWORD", "missing container password") from error


def fixture_password(label: str) -> str:
    """Derive non-persisted fixture passwords from the private container secret."""
    digest = hashlib.pbkdf2_hmac("sha256", default_password().encode(), ("upgrade-password:" + label).encode(), 600_000).hex()
    return "Upg!" + digest[:28]


def secret_value(label: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", default_password().encode(), ("upgrade-secret:" + label).encode(), 600_000).hex()


def legacy_token_expiry(token: str) -> float:
    """Read only the untrusted expiry claim; never log the token or claims."""
    try:
        segment = token.split(".")[1]
        encoded_payload = segment + "=" * (-len(segment) % 4)
        expiry = json.loads(base64.urlsafe_b64decode(encoded_payload))["exp"]
    except (IndexError, KeyError, ValueError, json.JSONDecodeError) as error:
        raise ScenarioError("verify", "legacy token", "could not read JWT expiry safely") from error
    if not isinstance(expiry, (int, float)):
        raise ScenarioError("verify", "legacy token", "JWT expiry was not numeric")
    return float(expiry)


def login(http: Http, email: str, password: str) -> str:
    body = http.json("POST", "/auth/login", form={"username": email, "password": password})
    token = body.get("access_token") if isinstance(body, dict) else None
    require(isinstance(token, str) and token, http.phase, "/auth/login", "login did not return access token")
    return token


def invite_user(http: Http, admin_token: str, *, email: str, name: str, organization_id: str, label: str) -> str:
    user = http.json(
        "POST", "/api/users", token=admin_token,
        payload={"email": email, "name": name, "organization_id": organization_id, "is_superuser": False},
        expected=(201,),
    )
    user_id = object_id(user, http.phase, "/api/users")
    registration_url = user.get("registration_url")
    require(isinstance(registration_url, str), http.phase, "/api/users", "invite omitted registration URL")
    invite_token = urllib.parse.parse_qs(urllib.parse.urlparse(registration_url).query).get("token", [None])[0]
    require(isinstance(invite_token, str) and invite_token, http.phase, "/auth/register-from-invite", "invite URL omitted token")
    registered = http.json(
        "POST", "/auth/register-from-invite",
        payload={"token": invite_token, "password": fixture_password(label), "name": name},
        expected=(200,),
    )
    require(registered.get("is_registered") is True, http.phase, "/auth/register-from-invite", "invite registration did not complete")
    return user_id



def active_deployment_id(app_id: str, phase: str) -> str:
    """Read the internal activation pointer omitted from the public App DTO."""
    import asyncio
    from uuid import UUID
    from sqlalchemy import text
    from src.core.database import get_db_context, get_engine

    async def read() -> str:
        try:
            async with get_db_context() as db:
                value = await db.scalar(
                    text("SELECT active_deployment_id FROM applications WHERE id=:app_id"),
                    {"app_id": UUID(app_id)},
                )
            require(value is not None, phase, "/api/applications", "App has no active deployment")
            return str(value)
        finally:
            # The next observation runs in another asyncio.run loop. Return no
            # asyncpg connection to a pool bound to the loop being closed here.
            await get_engine().dispose()

    return asyncio.run(read())


def source_zip() -> bytes:
    files = {
        "package.json": json.dumps({"name": "stable-2-upgrade-probe", "private": True, "scripts": {"build": "vite build"}, "devDependencies": {"vite": "6.2.2"}}),
        "index.html": '<meta name="bifrost-app-runtime" content="mount-v1"><div id="root"></div><script type="module" src="/src/main.js"></script>',
        "src/main.js": "import { tables } from 'bifrost';\nexport function mount(mountEl) { mountEl.textContent = `stable-2 upgrade fixture sdk=${Boolean(tables)}`; return () => mountEl.replaceChildren(); }\n(window.__BIFROST_APP_MODULES__ ??= new Map()).set(import.meta.url, { mount });\n",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


def assert_retained_source(archive_bytes: bytes, phase: str, path: str) -> None:
    """Verify the retained archive preserves the SDK-bound source, not just ZIP magic."""
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as actual, zipfile.ZipFile(io.BytesIO(source_zip())) as expected:
            for name in expected.namelist():
                require(actual.read(name) == expected.read(name), phase, path, "retained source file changed")
    except (KeyError, OSError, zipfile.BadZipFile) as error:
        raise ScenarioError(phase, path, "retained source archive was incomplete") from error


def active_bundle_fingerprint(http: Http, app_id: str, phase: str, admin_token: str) -> dict[str, Any]:
    """Fingerprint the served entry artifact, not just its deployment pointer."""
    manifest = http.json("GET", f"/api/applications/{app_id}/bundle-manifest", token=admin_token)
    entry = manifest.get("entry") if isinstance(manifest, dict) else None
    require(isinstance(entry, str) and entry, phase, "/api/applications/bundle-manifest", "active legacy bundle omitted entry artifact")
    require(manifest.get("runtime_contract") == "mount-v1", phase, "/api/applications/bundle-manifest", "active legacy bundle lost mount contract")
    artifact = http.request("GET", f"/api/applications/{app_id}/dist/{entry}", token=admin_token)
    require(bool(artifact.body), phase, "/api/applications/dist", "active legacy bundle artifact was empty")
    return {"entry": entry, "artifact_sha256": hashlib.sha256(artifact.body).hexdigest(), "artifact_bytes": len(artifact.body)}


def assert_active_legacy_bundle(http: Http, state: dict[str, Any], phase: str, admin_token: str) -> dict[str, Any]:
    """Prove the source V2 deployment and artifact survive without rebuilding."""
    application = state["application"]
    app_id, app_slug = application["id"], application["slug"]
    app = http.json("GET", f"/api/applications/{app_slug}", token=admin_token)
    deployment_id = active_deployment_id(app_id, phase)
    require(deployment_id == application["deployment_id"], phase, "/api/applications", "legacy application deployment changed")
    require(app.get("sdk_contract_version") == 2, phase, "/api/applications", "legacy application contract changed")
    source = http.request("GET", f"/api/applications/{app_id}/source", token=admin_token)
    assert_retained_source(source.body, phase, "/api/applications/source")
    bundle = active_bundle_fingerprint(http, app_id, phase, admin_token)
    require(bundle == application["bundle"], phase, "/api/applications/dist", "legacy application artifact changed")
    return {"deployment_id": deployment_id, "sdk_contract_version": app["sdk_contract_version"], **bundle}


def wait_terminal(http: Http, path: str, *, token: str, timeout: int, success: tuple[str, ...]) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = http.json("GET", path, token=token)
        require(isinstance(body, dict), http.phase, path, "terminal resource was not an object")
        status = str(body.get("status", "")).lower()
        if status in success:
            return body
        if status in {"failed", "failure", "cancelled", "canceled", "error", "timed_out", "timeout"}:
            raise ScenarioError(http.phase, path, f"terminal failure ({status})")
        time.sleep(POLL_SECONDS)
    raise ScenarioError(http.phase, path, "timed out waiting for terminal result")


def create_integration(http: Http, token: str, *, name: str, organization_id: str, entity_id: str, secret: str) -> tuple[str, str]:
    integration = http.json(
        "POST", "/api/integrations", token=token,
        payload={"name": name, "config_schema": [
            {"key": "base_url", "type": "string", "required": True},
            {"key": "api_key", "type": "secret", "required": True},
        ]}, expected=(201,),
    )
    integration_id = object_id(integration, http.phase, "/api/integrations")
    http.json(
        "PUT", f"/api/integrations/{integration_id}/config", token=token,
        payload={"config": {"base_url": "https://example.invalid", "api_key": secret}},
    )
    mapping = http.json(
        "POST", f"/api/integrations/{integration_id}/mappings", token=token,
        payload={"organization_id": organization_id, "entity_id": entity_id, "entity_name": entity_id}, expected=(201,),
    )
    return integration_id, object_id(mapping, http.phase, f"/api/integrations/{integration_id}/mappings")


def seed() -> dict[str, Any]:
    http = Http("seed")
    admin_email = os.environ["BIFROST_DEFAULT_USER_EMAIL"]
    admin_token = login(http, admin_email, default_password())
    org_a = http.json("POST", "/api/organizations", token=admin_token, payload={"name": "Stable 2 Upgrade Org A", "domain": "stable2-a.invalid"}, expected=(201,))
    org_b = http.json("POST", "/api/organizations", token=admin_token, payload={"name": "Stable 2 Upgrade Org B", "domain": "stable2-b.invalid"}, expected=(201,))
    org_a_id, org_b_id = object_id(org_a, "seed", "/api/organizations"), object_id(org_b, "seed", "/api/organizations")

    role_a = http.json("POST", "/api/roles", token=admin_token, payload={"name": "Stable 2 Org A Automation", "description": "Upgrade fixture access for Org A"}, expected=(201,))
    role_b = http.json("POST", "/api/roles", token=admin_token, payload={"name": "Stable 2 Org B Observer", "description": "Upgrade fixture access for Org B"}, expected=(201,))
    role_a_id, role_b_id = object_id(role_a, "seed", "/api/roles"), object_id(role_b, "seed", "/api/roles")
    user_a_email, user_b_email = "stable2-human-a@gobifrost.dev", "stable2-human-b@gobifrost.dev"
    user_a_id = invite_user(http, admin_token, email=user_a_email, name="Stable 2 Human A", organization_id=org_a_id, label="human-a")
    user_b_id = invite_user(http, admin_token, email=user_b_email, name="Stable 2 Human B", organization_id=org_b_id, label="human-b")
    http.request("POST", f"/api/roles/{role_a_id}/users", token=admin_token, payload={"user_ids": [user_a_id]}, expected=(204,))
    http.request("POST", f"/api/roles/{role_b_id}/users", token=admin_token, payload={"user_ids": [user_b_id]}, expected=(204,))

    user_c_email = "stable2-human-a-without-role@gobifrost.dev"
    user_c_id = invite_user(http, admin_token, email=user_c_email, name="Stable 2 Human A Without Role", organization_id=org_a_id, label="human-a-without-role")
    config_a_key, config_b_key = "stable2_upgrade_org_a", "stable2_upgrade_org_b"
    config_a_secret, config_b_secret = secret_value("config-a"), secret_value("config-b")
    for key, value, org_id in ((config_a_key, config_a_secret, org_a_id), (config_b_key, config_b_secret, org_b_id)):
        http.request("POST", "/api/sdk/config/set", token=admin_token, payload={"key": key, "value": value, "is_secret": True, "scope": org_id}, expected=(204,))
    integration_a_id, mapping_a_id = create_integration(http, admin_token, name="Stable 2 Integration A", organization_id=org_a_id, entity_id="stable2-tenant-a", secret=secret_value("integration-a"))
    integration_b_id, mapping_b_id = create_integration(http, admin_token, name="Stable 2 Integration B", organization_id=org_b_id, entity_id="stable2-tenant-b", secret=secret_value("integration-b"))

    workflow_name, workflow_path = "stable_2_upgrade_tool", "workflows/stable_2_upgrade_tool.py"
    workflow_source = f'''import hashlib
from bifrost import workflow, config, integrations

@workflow(name="{workflow_name}", is_tool=True)
async def {workflow_name}():
    config_ok = hashlib.sha256((await config.get("{config_a_key}")).encode()).hexdigest() == "{hashlib.sha256(config_a_secret.encode()).hexdigest()}"
    integration = await integrations.get("Stable 2 Integration A")
    integration_ok = hashlib.sha256(integration.config.get("api_key", "").encode()).hexdigest() == "{hashlib.sha256(secret_value("integration-a").encode()).hexdigest()}" if integration else False
    return {{"config_ok": config_ok, "integration_ok": integration_ok, "entity": integration.entity_id if integration else None}}
'''
    http.request("PUT", "/api/files/editor/content", token=admin_token, payload={"path": workflow_path, "content": workflow_source, "encoding": "utf-8"}, expected=(200, 201))
    workflow = http.json("POST", "/api/workflows/register", token=admin_token, payload={"path": workflow_path, "function_name": workflow_name, "organization_id": None, "access_level": "role_based", "role_ids": [role_a_id]}, expected=(200, 201))
    workflow_id = object_id(workflow, "seed", "/api/workflows/register")
    form = http.json("POST", "/api/forms", token=admin_token, payload={"name": "Stable 2 Upgrade Form", "workflow_id": workflow_id, "form_schema": {"fields": [{"name": "message", "type": "text", "label": "Message", "required": True}]}, "access_level": "role_based", "organization_id": None, "role_ids": [role_a_id]}, expected=(201,))
    agent = http.json("POST", "/api/agents", token=admin_token, payload={"name": "Stable 2 Upgrade Agent", "system_prompt": "Validate the preserved tool binding.", "channels": ["chat"], "access_level": "role_based", "organization_id": None, "tool_ids": [workflow_id], "role_ids": [role_a_id]}, expected=(201,))

    app = http.json("POST", "/api/applications", token=admin_token, payload={"name": "Stable 2 Upgrade App", "slug": "stable-2-upgrade-app", "app_model": "standalone_v2", "organization_id": org_a_id, "access_level": "authenticated"}, expected=(201,))
    app_id = object_id(app, "seed", "/api/applications")
    accepted = http.json("POST", f"/api/applications/{app_id}/deploy", token=admin_token, multipart=("source", "stable-2-source.zip", source_zip()), expected=(202,))
    job_id = accepted.get("job_id")
    require(isinstance(job_id, str), "seed", f"/api/applications/{app_id}/deploy", "deploy omitted job id")
    deployed = wait_terminal(http, f"/api/platform-jobs/{job_id}", token=admin_token, timeout=JOB_TIMEOUT_SECONDS, success=("succeeded",))
    deployed_app = http.json("GET", "/api/applications/stable-2-upgrade-app", token=admin_token)
    deployment_id = active_deployment_id(app_id, "seed")
    require(deployed.get("result", {}).get("deployment_id") == deployment_id, "seed", "/api/applications/deploy", "deploy result does not match active deployment")
    require(deployed_app.get("sdk_contract_version") == 2, "seed", "/api/applications", "source App did not record SDK contract 2")
    source = http.request("GET", f"/api/applications/{app_id}/source", token=admin_token)
    assert_retained_source(source.body, "seed", f"/api/applications/{app_id}/source")
    bundle = active_bundle_fingerprint(http, app_id, "seed", admin_token)
    # Obtain this only after the slow deploy so rejection cannot be explained by
    # the ordinary lifetime of an early seed login.
    legacy_access_token = login(http, admin_email, default_password())
    require(http.status("GET", "/api/organizations", token=legacy_access_token) == 200, "seed", "/api/organizations", "newly issued legacy token was not accepted")
    return {
        "state": {
            "organizations": {"a": org_a_id, "b": org_b_id},
            "users": {"a": {"id": user_a_id, "email": user_a_email}, "b": {"id": user_b_id, "email": user_b_email}, "a_without_role": {"id": user_c_id, "email": user_c_email}},
            "roles": {"a": role_a_id, "b": role_b_id},
            "configs": {"a_key": config_a_key, "b_key": config_b_key, "secret_length": len(config_a_secret)},
            "integrations": {"a": integration_a_id, "b": integration_b_id, "a_mapping": mapping_a_id, "b_mapping": mapping_b_id},
            "workflow": {"id": workflow_id, "path": workflow_path, "name": workflow_name},
            "form": {"id": object_id(form, "seed", "/api/forms")},
            "agent": {"id": object_id(agent, "seed", "/api/agents")},
            "application": {"id": app_id, "slug": "stable-2-upgrade-app", "deployment_id": deployment_id, "deploy_job_id": job_id, "bundle": bundle},
        },
        "evidence": {"seeded_two_orgs": True, "invites_registered": True, "role_memberships": True, "secret_configs_seeded": True, "secret_integration_configs_seeded": True, "registered_tool_linked_to_form_and_agent": True, "retained_v2_source": True, "deploy_job_terminal": bool(deployed)},
        "legacy_access_token": legacy_access_token,
    }


def verify(seed_payload: dict[str, Any]) -> dict[str, Any]:
    phase, http = "verify", Http("verify")
    state = seed_payload.get("state")
    legacy_token = seed_payload.get("legacy_access_token")
    require(isinstance(state, dict), phase, "stdin", "seed state missing")
    require(isinstance(legacy_token, str) and legacy_token, phase, "stdin", "legacy token missing")
    users, orgs = state["users"], state["organizations"]
    user_a_token = login(http, users["a"]["email"], fixture_password("human-a"))
    user_b_token = login(http, users["b"]["email"], fixture_password("human-b"))
    user_c_token = login(http, users["a_without_role"]["email"], fixture_password("human-a-without-role"))
    admin_token = login(http, os.environ["BIFROST_DEFAULT_USER_EMAIL"], default_password())
    legacy_bundle = assert_active_legacy_bundle(http, state, phase, admin_token)
    require(legacy_token_expiry(legacy_token) > time.time(), phase, "legacy token", "legacy token expired before rejection check")
    legacy_status = http.status("GET", "/api/organizations", token=legacy_token)
    require(legacy_status == 401, phase, "/api/organizations", "legacy access token was not rejected as unauthenticated")

    workflow = http.json("GET", f"/api/workflows/{state['workflow']['id']}", token=admin_token)
    form = http.json("GET", f"/api/forms/{state['form']['id']}", token=admin_token)
    agent = http.json("GET", f"/api/agents/{state['agent']['id']}", token=admin_token)
    require(workflow.get("id") == state["workflow"]["id"], phase, "/api/workflows", "workflow id changed")
    require(state["roles"]["a"] in workflow.get("role_ids", []), phase, "/api/workflows", "workflow role link missing")
    require(form.get("workflow_id") == state["workflow"]["id"], phase, "/api/forms", "form workflow link changed")
    require(state["workflow"]["id"] in agent.get("tool_ids", []), phase, "/api/agents", "agent tool link changed")
    require(state["roles"]["a"] in agent.get("role_ids", []), phase, "/api/agents", "agent role link missing")

    own_form_status = http.status("GET", f"/api/forms/{state['form']['id']}", token=user_a_token)
    other_form_status = http.status("GET", f"/api/forms/{state['form']['id']}", token=user_b_token)
    no_role_form_status = http.status("GET", f"/api/forms/{state['form']['id']}", token=user_c_token)
    require(own_form_status == 200, phase, "/api/forms", "role member lost form access")
    require(other_form_status in (403, 404), phase, "/api/forms", "other organization gained role access")
    require(no_role_form_status in (403, 404), phase, "/api/forms", "same organization user without role gained access")
    app_slug = state["application"]["slug"]
    own_app_status = http.status("GET", f"/api/applications/{app_slug}", token=user_a_token)
    other_app_status = http.status("GET", f"/api/applications/{app_slug}", token=user_b_token)
    require(own_app_status == 200, phase, "/api/applications", "own organization lost app access")
    require(other_app_status in (403, 404), phase, "/api/applications", "other organization gained app access")

    for integration_key, org_key, mapping_key in (("a", "a", "a_mapping"), ("b", "b", "b_mapping")):
        integration = http.json("GET", f"/api/integrations/{state['integrations'][integration_key]}", token=admin_token)
        require(integration.get("id") == state["integrations"][integration_key], phase, "/api/integrations", "integration id changed")
        mappings = integration.get("mappings", [])
        require(any(item.get("id") == state["integrations"][mapping_key] and item.get("organization_id") == orgs[org_key] for item in mappings), phase, "/api/integrations", "integration mapping changed")

    submitted = http.json("POST", "/api/workflows/execute", token=user_a_token, payload={"workflow_id": state["workflow"]["id"], "input_data": {}, "sync": False})
    execution_id = submitted.get("execution_id")
    require(isinstance(execution_id, str), phase, "/api/workflows/execute", "execution id missing")
    execution = wait_terminal(http, f"/api/executions/{execution_id}", token=user_a_token, timeout=EXECUTION_TIMEOUT_SECONDS, success=("success",))
    result = execution.get("result") or {}
    require(result.get("config_ok") is True and result.get("integration_ok") is True and result.get("entity") == "stable2-tenant-a", phase, "/api/executions", "workflow did not decrypt preserved synthetic credentials")

    return {"state": state, "evidence": {"new_human_logins": True, "legacy_token_future_before_rejection": True, "legacy_access_rejected": True, "role_and_org_isolation": True, "resource_ids_and_links_preserved": True, "integration_mapping_ids_preserved": True, "worker_decrypted_synthetic_credentials": True, "registered_automation_succeeded": True, "legacy_app_active_bundle_verified_before_rebuild": True,
        "registered_execution": {"id": execution_id, "status": execution["status"], "result": result},
        "legacy_active_bundle": legacy_bundle,
    }}


def main(argv: list[str]) -> None:
    if len(argv) != 2 or argv[1] not in {"seed", "verify"}:
        raise ScenarioError("setup", "argv", "usage: stable_2_upgrade_population.py seed|verify")
    try:
        incoming = json.load(sys.stdin)
    except json.JSONDecodeError as error:
        raise ScenarioError("setup", "stdin", "input was not JSON") from error
    if argv[1] == "seed":
        result = seed()
    else:
        require(isinstance(incoming, dict), "verify", "stdin", "seed payload was not an object")
        result = verify(incoming)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main(sys.argv)
    except ScenarioError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
