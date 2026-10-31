"""Opt-in real-API/Pyodide qualification, restricted to the disposable test stack.

Fixture rows use the existing committed token-test world. They are not an
execution adapter writing business-job state. This does not claim dispatch,
ownership or terminal acceptance, which need separate canonical conformance.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from uuid import uuid4
from urllib.parse import urlsplit

import httpx


@asynccontextmanager
async def seeded_config(api, headers, key, scope):
    response = await api.post("/api/sdk/config/set", headers=headers,
                              json={"key": key, "value": 42, "is_secret": False, "scope": scope})
    try:
        assert response.status_code == 204, f"Canonical runtime credential seed status {response.status_code}"
        yield
    finally:
        if response.is_success:
            deleted = await api.post("/api/sdk/config/delete", headers=headers, json={"key": key, "scope": scope})
            assert deleted.status_code == 200, f"Fixture config cleanup status {deleted.status_code}"


@asynccontextmanager
async def cleanup_fixture_audit(world, engine):
    """Same exact-fixture audit teardown as upstream worker SDK HTTP tests."""
    from sqlalchemy import delete, or_
    from sqlalchemy.ext.asyncio import AsyncSession
    from src.models.orm import AuditLog, Execution, Organization, User

    try:
        yield
    finally:
        orgs = [row_id for model, row_id in world._created if model is Organization]
        users = [row_id for model, row_id in world._created if model is User]
        executions = [row_id for model, row_id in world._created if model is Execution]
        async with AsyncSession(engine) as session:
            await session.execute(delete(AuditLog).where(or_(AuditLog.organization_id.in_(orgs),
                AuditLog.user_id.in_(users), AuditLog.execution_id.in_(executions))))
            await session.commit()


async def main() -> None:
    if os.environ.get("BIFROST_ENVIRONMENT") != "testing" or os.environ.get("TEST_API_URL") != "http://api:8000":
        raise RuntimeError("Only the repository disposable Docker test stack is admitted")
    database = urlsplit(os.environ.get("BIFROST_DATABASE_URL", ""))
    if database.hostname != "pgbouncer" or database.path != "/bifrost_test":
        raise RuntimeError("Disposable test database target is required")

    from src.core.database import close_db, get_engine, init_db
    from src.core.module_cache import invalidate_module, set_module
    from src.services.authorization.tokens import RuntimeSubject, issue_run_credentials
    from src.services.repo_storage import RepoStorage
    from tests.unit.authorization.committed_runs import committed_world

    await init_db()
    storage = RepoStorage()
    suffix = uuid4().hex
    path = f"workflows/pyodide_qualification_{suffix}.py"
    helper = f"pyodide_helper_{suffix}"
    helper_path = f"{helper}.py"
    config_key = f"pyodide-qualification-{suffix}"
    table = f"pyodide-qualification-{suffix}"
    source = f'''
import logging
import os
from bifrost import workflow, config, tables
from {helper} import fold

@workflow
async def qualified(n: int = 7):
    value = await config.get({config_key!r})
    document = await tables.upsert({table!r}, id="proof", data={{"value": value + fold(n)}})
    logging.getLogger(__name__).warning("authorized local Pyodide workflow")
    return {{"value": document.data["value"], "parent_secret_present": "BIFROST_SECRET_KEY" in os.environ,
             "credential_redaction_probe": os.environ["BIFROST_ACCESS_TOKEN"]}}
'''
    sources = {path: source, helper_path: "def fold(n):\n    return sum(range(1, n + 1))\n"}
    manifest = {name: hashlib.sha256(code.encode()).hexdigest() for name, code in sources.items()}
    report = {"runtime": "local-workerd-real-upstream-worker", "canonical_claim_verified": False,
              "canonical_terminal_acceptance_verified": False, "workspace_sha256": manifest}
    access_token = None
    try:
        async with committed_world(get_engine()) as world, cleanup_fixture_audit(world, get_engine()):
            organization = await world.organization()
            other = await world.organization()
            user = await world.person(organization)
            role = await world.role("configs.read", "configs.readwrite", "tables.read", "tables.readwrite")
            await world.assign(user, role.id, ("organization", organization.id))
            workflow = await world.workflow(organization)
            workflow.path = path
            workflow.function_name = "qualified"
            execution = await world.execution(run_user=user, workflow=workflow, organization=organization,
                                              credential_version=2)
            execution.workflow_name = workflow.name
            await world.session.commit()
            for name, code in sources.items():
                digest = await storage.write(name, code.encode())
                assert digest == manifest[name]
                await set_module(name, code, digest)

            credentials = await issue_run_credentials(RuntimeSubject("workflow_execution", execution.id),
                                                      protocol=2, legacy_lifetime_seconds=300)
            access_token = credentials.access_token
            headers = {"Authorization": f"Bearer {access_token}"}
            async with httpx.AsyncClient(base_url="http://api:8000", timeout=60, follow_redirects=False) as api, seeded_config(
                api, headers, config_key, str(organization.id)
            ):
                denied = await api.post("/api/sdk/config/get", headers=headers,
                                       json={"key": config_key, "scope": str(other.id)})
                assert denied.status_code == 403, f"Cross-tenant status {denied.status_code}"
                context = {"execution_id": str(execution.id), "workflow_id": str(workflow.id),
                           "name": workflow.name, "function_name": "qualified", "file_path": path,
                           "content_hash": manifest[path], "parameters": {"n": 7},
                           "caller": {"user_id": str(user.id), "email": user.email, "name": user.name},
                           "organization": {"id": str(organization.id), "name": organization.name, "is_active": True,
                                            "is_provider": False},
                           "engine_token": access_token, "engine_refresh_token": credentials.refresh_token,
                           "timeout_seconds": 30, "transient": True, "no_cache": True}
                async with httpx.AsyncClient(timeout=90, follow_redirects=False) as provider:
                    response = await provider.post("http://bifrost-pyodide-ddf316c1:8799",
                                                   json={"execution_id": str(execution.id), "context": context,
                                                         "manifest": manifest})
                    assert response.status_code == 200, f"Local provider status {response.status_code}"
                    evidence = response.json()
                encoded = json.dumps(evidence)
                if access_token in encoded or (credentials.refresh_token and credentials.refresh_token in encoded):
                    raise RuntimeError("Runtime credential was exposed; evidence not emitted")
                result = evidence["result"]
                if result.get("status") != "Success":
                    report["failure"] = evidence
                    print(json.dumps(report))
                    raise RuntimeError("Actual upstream worker did not complete successfully")
                assert result["result"]["value"] == 70
                assert result["result"]["parent_secret_present"] is False
                assert result["metrics"]["code_hash"] == manifest[path]
                readback = await api.get(f"/api/tables/{table}/documents/proof", headers=headers,
                                         params={"scope": str(organization.id)})
                assert readback.status_code == 200
                assert readback.json()["data"]["value"] == 70
                report.update({"result": evidence, "independent_document_value": 70,
                               "cross_tenant_status": denied.status_code,
                               "credential_version": credentials.credential_version})
            # Vary only the fixture run's liveness while its person/roles remain.
            # This is an authorization test input, not canonical terminal reporting.
            from src.models.enums import ExecutionStatus
            async with httpx.AsyncClient(base_url="http://api:8000", timeout=15) as api:
                before = await api.post("/api/sdk/config/get", headers=headers, json={"key": config_key})
                assert before.status_code == 200
                execution.status = ExecutionStatus.SUCCESS
                await world.session.commit()
                terminal = await api.post("/api/sdk/config/get", headers=headers, json={"key": config_key})
                assert terminal.status_code == 401
                report["live_then_fixture_terminal_status"] = [before.status_code, terminal.status_code]
        # Deleting the fixture execution removes its live-run authority, regardless
        # of the token expiry. No persistent caller credential is created.
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as api:
            ended = await api.post("http://api:8000/api/sdk/config/get",
                                   headers={"Authorization": f"Bearer {access_token}"},
                                   json={"key": config_key})
            assert ended.status_code == 401
            report["ended_run_status"] = ended.status_code
        print(json.dumps(report))
    finally:
        for name in sources:
            await invalidate_module(name)
            await storage.delete(name)
        await close_db()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(main())
