"""Opt-in disposable-stack claim -> real Pyodide -> canonical completion proof.

Trusted fixed fixture only. Not production provider admission or physical stop.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import runpy
import time
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, select


async def main(*, isolation_label=None, batch=None, refresh_probe=False, expired_access=False,
               concurrent_refresh=False, interrupt_refresh=False, failure_probe=False, transport_waiter=False,
               node_pyodide=False, integration_probe=False, integration_secret_probe=False, benchmark_probe=False):
    target = urlsplit(os.environ.get("BIFROST_DATABASE_URL", ""))
    if (os.environ.get("BIFROST_ENVIRONMENT") != "testing"
        or os.environ.get("TEST_API_URL") != "http://api:8000"
        or target.hostname != "pgbouncer" or target.path != "/bifrost_test"):
        raise RuntimeError("Disposable Docker test stack required")
    if isolation_label is not None and (isolation_label not in ("tenant-a", "tenant-b")
                                       or not re.fullmatch(r"[a-f0-9]{32}", batch or "")):
        raise RuntimeError("Fixed two-tenant isolation fixture required")
    if refresh_probe and isolation_label is not None:
        raise RuntimeError("Refresh qualification is a separate fixed single-run fixture")
    if expired_access and (refresh_probe or isolation_label is not None):
        raise RuntimeError("Expired-access qualification is a separate fixed single-run fixture")
    if concurrent_refresh and (refresh_probe or expired_access or isolation_label is not None):
        raise RuntimeError("Concurrent refresh is a separate fixed single-run fixture")
    if interrupt_refresh and (refresh_probe or expired_access or concurrent_refresh or isolation_label is not None):
        raise RuntimeError("Refresh interruption is a separate fixed single-run fixture")
    if failure_probe and (refresh_probe or expired_access or concurrent_refresh or interrupt_refresh or isolation_label is not None):
        raise RuntimeError("Failure qualification is a separate fixed single-run fixture")
    expected_success = not failure_probe
    if benchmark_probe and (not integration_probe or failure_probe or refresh_probe or expired_access
                            or concurrent_refresh or interrupt_refresh or isolation_label is not None or transport_waiter):
        raise RuntimeError("Benchmark requires the fixed successful integration fixture")
    if integration_secret_probe and not integration_probe:
        raise RuntimeError("Synthetic integration-secret qualification requires integration fixture")
    if integration_probe and (isolation_label is not None or refresh_probe or expired_access or concurrent_refresh
                              or interrupt_refresh or failure_probe or transport_waiter):
        raise RuntimeError("Integration qualification is a separate fixed single-run fixture")
    if transport_waiter and (refresh_probe or expired_access or concurrent_refresh or interrupt_refresh
                             or failure_probe or isolation_label is not None):
        raise RuntimeError("Transport waiter qualification is a separate fixed single-run fixture")

    from src.core.database import init_db, get_engine, close_db, get_db_context
    from src.core.module_cache import set_module, invalidate_module
    from src.core.cache import get_redis
    from src.core.redis_client import PendingExecution, pending_execution_record
    from src.jobs.consumers.workflow_execution import WorkflowExecutionConsumer
    from src.models.enums import ExecutionStatus
    from src.models.orm import Execution, ExecutionLog
    from src.models.orm.execution_workers import ExecutionWorker, ExecutionWorkerClaim
    from src.models.orm.work_dispatches import WorkDispatch
    from src.services.execution.process_pool import CredentialRoute, ExecutionInfo
    from src.services.execution.workflow_dispatch import stage_workflow_run
    from src.services.repo_storage import RepoStorage
    from src.services.worker_pool.claims import (
        claim_next, register_static_worker, mark_static_ready, heartbeat_worker,
    )
    from src.worker.remote.fencing import LeaseRef, remote_owner_context
    from src.worker.remote.ownership import acquire_remote_owner, renew_remote_owner, release_remote_owner
    from tests.unit.authorization.committed_runs import committed_world
    from shared.version import get_version

    # Reuse the existing fixture setup/teardown; loading definitions has no run effects.
    helpers = runpy.run_path("/spike/backend/authorized-local.py")
    journal_type = runpy.run_path("/spike/backend/attempt_journal.py")["AttemptJournal"]
    await init_db()
    suffix = uuid4().hex
    path = f"workflows/pyodide_qualification_{suffix}.py"
    helper = f"pyodide_helper_{suffix}"
    key, table = f"pyodide-config-{suffix}", f"pyodide-table-{suffix}"
    integration_name = f"pyodide-integration-{suffix}"
    integration_secret = f"synthetic-integration-{uuid4().hex}" if integration_secret_probe else None
    integration_secret_code = f'''    import hashlib
    synthetic_secret = integration.config.get("qualification_secret")
    if not isinstance(synthetic_secret, str) or hashlib.sha256(synthetic_secret.encode()).hexdigest() != {hashlib.sha256(integration_secret.encode()).hexdigest() if integration_secret else None!r}:
        raise RuntimeError("Synthetic integration secret was not decrypted correctly")
    isolation["integration_secret_probe"] = synthetic_secret
    logging.getLogger(__name__).warning("integration secret=%s", synthetic_secret)
''' if integration_secret_probe else ""
    canary_code = "    isolation = {}\n"
    if isolation_label is not None:
        canary_code = f'''    import builtins, json
    from pathlib import Path
    canary = Path("/tmp/bifrost-tenant-canary")
    isolation = {{"builtins_before": getattr(builtins, "_bifrost_tenant_canary", None),
                 "environment_before": os.environ.get("BIFROST_TENANT_CANARY"),
                 "module_before": getattr(json, "_bifrost_tenant_canary", None),
                 "tmp_before": canary.read_text() if canary.exists() else None,
                 "tenant_label": {isolation_label!r}}}
    if any(isolation[key] is not None for key in ("builtins_before", "environment_before", "module_before", "tmp_before")):
        raise RuntimeError("Cross-run state contamination before SDK effects")
    builtins._bifrost_tenant_canary = {isolation_label!r}
    json._bifrost_tenant_canary = {isolation_label!r}
    os.environ["BIFROST_TENANT_CANARY"] = {isolation_label!r}
    canary.write_text({isolation_label!r})
    import httpx
    try:
        await config.get(forbidden_key, scope=forbidden_scope)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 403:
            raise
        isolation["peer_sdk_status"] = exc.response.status_code
    else:
        raise RuntimeError("Peer tenant SDK read was not denied")
'''
    refresh_code = ("    from bifrost.client import get_client\n"
                    "    replacement = await get_client().refresh_access_token()\n"
                    "    if replacement is None:\n"
                    "        raise RuntimeError('SDK refresh returned no replacement credential')\n") if refresh_probe else ""
    if concurrent_refresh:
        refresh_code = '''    import asyncio
    from bifrost.client import get_client
    client = get_client()
    observed = os.environ["BIFROST_ACCESS_TOKEN"]
    ready = 0
    start = asyncio.Event()
    async def refresh_one(index):
        nonlocal ready
        ready += 1
        if ready == 3:
            start.set()
        await start.wait()
        if index == 2:
            return client._refresh_access_token_sync(observed)
        return await client.refresh_access_token(observed)
    replacements = await asyncio.gather(*(refresh_one(index) for index in range(3)))
    if any(token is None for token in replacements) or len(set(replacements)) != 1:
        raise RuntimeError("Concurrent SDK refresh did not converge")
    refresh_evidence = {"refresh_callers": ready, "same_replacement": True,
                        "synchronous_callers": 1, "asynchronous_callers": 2}
'''
    if interrupt_refresh:
        refresh_code = '''    import asyncio, httpx
    import pyodide_refresh
    from bifrost.client import get_client
    client = get_client()
    observed = os.environ["BIFROST_ACCESS_TOKEN"]
    waiter = asyncio.create_task(client.refresh_access_token(observed))
    async with httpx.AsyncClient(base_url="https://test-api.invalid", timeout=15) as gate:
        ready_response = await gate.get("/__fixture_refresh/ready")
        ready_response.raise_for_status()
        if ready_response.json().get("server_response_received") is not True:
            raise RuntimeError("Refresh response was not held")
        waiter.cancel()
        try:
            await waiter
        except asyncio.CancelledError:
            pass
        else:
            raise RuntimeError("Refresh waiter was not cancelled")
        pending = tuple(pyodide_refresh._pending)
        if len(pending) != 1:
            raise RuntimeError("Refresh core did not survive waiter cancellation")
        released = await gate.post("/__fixture_refresh/release")
        released.raise_for_status()
        await asyncio.gather(*pending)
    replacement = await client.refresh_access_token(observed)
    if replacement is None or replacement == observed:
        raise RuntimeError("Completed refresh credential was not adopted")
    if pyodide_refresh._pending:
        raise RuntimeError("Refresh work remained after credential adoption")
    refresh_evidence = {"cancelled_refresh_waiter": True, "pending_refresh_at_cancel": len(pending),
                        "server_response_received": True, "replacement_adopted": True,
                        "pending_refresh_after_adoption": len(pyodide_refresh._pending)}
'''
    failure_code = '''    print("stdout credential=" + os.environ["BIFROST_ACCESS_TOKEN"])
    logging.getLogger(__name__).warning("structured credential=%s", os.environ["BIFROST_ACCESS_TOKEN"])
    raise ValueError("fixture failure credential=" + os.environ["BIFROST_ACCESS_TOKEN"])
''' if failure_probe else ""
    integration_code = f'''    from bifrost import integrations
    import httpx
    integration = await integrations.get({integration_name!r})
    if integration is None or integration.config.get("qualification_value") != 42:
        raise RuntimeError("Integration configuration read did not match fixture")
    try:
        await integrations.get({integration_name!r}, scope=forbidden_scope)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 403:
            raise
        isolation["integration_peer_status"] = 403
    else:
        raise RuntimeError("Cross-tenant integration read was not denied")
    isolation["integration_value"] = integration.config["qualification_value"]
{integration_secret_code.rstrip()}
''' if integration_probe else ""
    benchmark_code = (f"    from {helper} import measure\n"
                      f"    refresh_evidence.update(await measure({key!r}, {integration_name!r}, {table!r}))\n") if benchmark_probe else ""
    source = f'''from bifrost import workflow, config, tables
from {helper} import fold
import logging, os
@workflow
async def qualified(n: int = 7, forbidden_scope: str | None = None, forbidden_key: str | None = None):
{canary_code.rstrip()}
    value = await config.get({key!r})
{integration_code.rstrip()}
    refresh_evidence = {{}}
{refresh_code.rstrip()}
{failure_code.rstrip()}
    doc = await tables.upsert({table!r}, id="proof", data={{"value": value + fold(n)}})
{benchmark_code.rstrip()}
    logging.getLogger(__name__).warning("canonical local Pyodide proof")
    return {{"value": doc.data["value"], "parent_secret_present": "BIFROST_SECRET_KEY" in os.environ,
             "credential_redaction_probe": os.environ["BIFROST_ACCESS_TOKEN"], **isolation, **refresh_evidence}}
'''
    sources = {path: source, f"{helper}.py": "def fold(n):\n    return sum(range(1,n+1))\n"}
    if benchmark_probe:
        sources[f"{helper}.py"] += Path(__file__).with_name("benchmark-workloads.py").read_text()
    manifest = {name: hashlib.sha256(code.encode()).hexdigest() for name, code in sources.items()}
    report = {"runtime": "node-pyodide-canonical-claim-consumer" if node_pyodide else "local-workerd-canonical-claim-consumer", "workspace_sha256": manifest,
              "physical_termination_verified": False, "cloud_invocations": 0}
    if refresh_probe:
        report["refresh_probe"] = True
    if expired_access:
        report["expired_access_probe"] = True
    if concurrent_refresh:
        report["concurrent_refresh_probe"] = True
    if interrupt_refresh:
        report["refresh_interruption_probe"] = True
    if failure_probe:
        report["workflow_failure_probe"] = True
    if isolation_label is not None:
        report["isolation_label"] = isolation_label
        report["rendezvous_batch"] = batch

    async def rendezvous(phase, value):
        peer = "tenant-b" if isolation_label == "tenant-a" else "tenant-a"
        prefix = f"bifrost:pyodide-isolation:{batch}:{phase}:"
        async with get_redis() as redis:
            await redis.set(prefix + isolation_label, json.dumps(value), ex=90)
            async with asyncio.timeout(60):
                while (other_value := await redis.get(prefix + peer)) is None:
                    if await redis.exists(f"bifrost:pyodide-isolation:{batch}:abort:{peer}"):
                        raise RuntimeError("Peer isolation fixture failed")
                    await asyncio.sleep(0.05)
            return json.loads(other_value)
    secrets = []
    if integration_secret is not None:
        secrets.append(integration_secret)
    storage = RepoStorage()
    heartbeat = None
    ref = None
    try:
        async with committed_world(get_engine()) as world, helpers["cleanup_fixture_audit"](world, get_engine()):
            org, other = await world.organization(), await world.organization()
            user = await world.person(org)
            permissions = ("configs.read", "configs.readwrite", "tables.read", "tables.readwrite")
            role = await world.role(*permissions, *(("integrations.read", "secrets.read") if integration_probe else ()))
            if integration_probe:
                from src.models.orm.integrations import Integration, IntegrationConfigSchema, IntegrationMapping
                from src.models.orm.config import Config
                from src.models.enums import ConfigType
                integration = await world._add(Integration(name=integration_name))
                await world._add(IntegrationMapping(integration_id=integration.id, organization_id=org.id,
                    entity_id="synthetic-qualification-entity"))
                schema = await world._add(IntegrationConfigSchema(integration_id=integration.id,
                    key="qualification_value", type="int"))
                await world._add(Config(key="qualification_value", value={"value": 42}, config_type=ConfigType.INT,
                    organization_id=org.id, integration_id=integration.id, config_schema_id=schema.id,
                    updated_by=str(user.id)))
                if integration_secret_probe:
                    from src.core.security import encrypt_secret
                    secret_schema = await world._add(IntegrationConfigSchema(integration_id=integration.id,
                        key="qualification_secret", type="secret"))
                    await world._add(Config(key="qualification_secret", value={"value": encrypt_secret(integration_secret)},
                        config_type=ConfigType.SECRET, organization_id=org.id, integration_id=integration.id,
                        config_schema_id=secret_schema.id, updated_by=str(user.id)))
            await world.assign(user, role.id, ("organization", org.id))
            workflow = await world.workflow(org)
            workflow.path, workflow.function_name, workflow.timeout_seconds = path, "qualified", 30
            execution = await world.execution(run_user=user, workflow=workflow, organization=org, credential_version=2)
            execution.status = ExecutionStatus.PENDING
            parameters = {"n": 7}
            if integration_probe:
                parameters["forbidden_scope"] = str(other.id)
            if isolation_label is not None:
                peer = await rendezvous("setup", {"organization_id": str(org.id), "key": key})
                parameters.update({"n": 7 if isolation_label == "tenant-a" else 8,
                                   "forbidden_scope": peer["organization_id"], "forbidden_key": peer["key"]})
                report["organization_id"] = str(org.id)
                report["peer_organization_id"] = peer["organization_id"]
            expected_value = 42 + sum(range(1, parameters["n"] + 1))
            pending = pending_execution_record(execution_id=str(execution.id), workflow_id=str(workflow.id),
                parameters=parameters, org_id=str(org.id), user_id=str(user.id), user_name=user.name,
                user_email=user.email, sync=False, lineage=None)
            accepted = await stage_workflow_run(world.session, pending,
                {"execution_id": str(execution.id), "workflow_id": str(workflow.id), "sync": False}, record_exists=True)
            await world.session.commit()
            for name, code in sources.items():
                written_digest = await storage.write(name, code.encode())
                assert written_digest == manifest[name]
                await set_module(name, code, manifest[name])
            worker_id = await register_static_worker(hostname=f"pyodide-local-fixture-{suffix}",
                supported_handlers=["workflow.execute"], version=get_version())
            world._created.append((ExecutionWorker, worker_id))
            worker = await world.session.get(ExecutionWorker, worker_id)
            assert worker is not None
            if isolation_label is not None:
                # The pool still schedules globally. Reserve this controlled
                # fixture dispatch; do not invent a tenant-specific scheduler.
                worker.reserved_for_dispatch_id = accepted.dispatch_id
            await world.session.commit()
            owner_id, launcher_id = uuid4(), uuid4()

            async def connect(sequence):
                lease = await acquire_remote_owner(worker_id=worker.id, owner_id=owner_id, launcher_id=launcher_id,
                    nonce_digest=f"{sequence:064x}", hello_sequence=sequence, credential_protocol=2)
                assert lease is not None
                return lease

            ref = LeaseRef(await connect(1))
            liveness = {"active_runs": 0, "runs_handled": 0, "memory_reserved_bytes": 0}
            report["worker_heartbeat_count"] = 0
            report["owner_renewal_count"] = 0

            async def beat():
                lease = ref.lease
                with remote_owner_context(ref):
                    state = await heartbeat_worker(worker.id, **liveness, package_generation=0,
                        memory_pressured=False, memory_budget_bytes=2**30)
                if lease != ref.lease:
                    return  # Same stale-renew guard as the upstream remote host.
                if state not in ("ready", "draining"):
                    raise ConnectionError("Canonical worker liveness rejected")
                report["worker_heartbeat_count"] += 1

            with remote_owner_context(ref):
                ready = await mark_static_ready(worker.id, memory_budget_bytes=2**30)
                assert ready
            await beat()

            async def renew():
                while True:
                    await asyncio.sleep(5)
                    lease = ref.lease
                    renewed = await renew_remote_owner(lease)
                    if lease != ref.lease:
                        continue
                    if not renewed:
                        raise ConnectionError("Canonical owner renewal failed")
                    report["owner_renewal_count"] += 1
                    await beat()

            heartbeat = asyncio.create_task(renew())
            journal = journal_type()

            class LocalPool:
                launches = 0
                evidence = None
                info = None
                token = None
                refresh_token = None

                def credential_route(self):
                    return CredentialRoute(2, ref.lease.session_generation)

                async def route_execution(self, *, execution_id, context, credential_route, active_execution):
                    provider_sent = asyncio.Event()
                    self.provider_sent = provider_sent

                    async def trace_provider(event, _info):
                        # Observe only the fixed provider request's transport phase;
                        # never retain headers, request bodies or credentials.
                        if event == "http11.send_request_body.complete":
                            provider_sent.set()

                    if credential_route != self.credential_route():
                        raise ConnectionError("Credential route changed")
                    assert context["file_path"] == path and context["function_name"] == "qualified"
                    context["content_hash"] = manifest[path]
                    self.token = context["engine_token"]
                    self.refresh_token = context.get("engine_refresh_token")
                    secrets.extend([self.token, context.get("engine_refresh_token")])
                    self.info = ExecutionInfo(execution_id, datetime.now(timezone.utc), context["timeout_seconds"], active_execution)

                    async def launch():
                        self.launches += 1
                        headers = {"Authorization": f"Bearer {self.token}"}
                        async with httpx.AsyncClient(base_url="http://api:8000", timeout=60) as api:
                            async with helpers["seeded_config"](api, headers, key, str(org.id)):
                                denied = await api.post("/api/sdk/config/get", headers=headers,
                                                        json={"key": key, "scope": str(other.id)})
                                assert denied.status_code == 403
                                if isolation_label is not None:
                                    await rendezvous("prepared", {"ready": True})
                                    report["provider_start_ms"] = int(time.time() * 1000)
                                child_context = context
                                if expired_access:
                                    from unittest.mock import patch
                                    from src.services.authorization.tokens import RuntimeSubject, issue_access_token

                                    # Only this fixture issuer's clock changes; the API, owner
                                    # lease and original parent credential retain real time.
                                    with patch("src.services.authorization.tokens._now", return_value=int(time.time()) - 7200):
                                        expired = await issue_access_token(RuntimeSubject("workflow_execution", execution.id))
                                    assert expired.expires_at < datetime.now(timezone.utc)
                                    secrets.append(expired.token)
                                    challenged = await api.get(f"/api/sdk/modules/{path}",
                                        headers={"Authorization": f"Bearer {expired.token}"})
                                    assert challenged.status_code == 401
                                    assert "refresh_required" in challenged.headers.get("www-authenticate", "")
                                    report["preflight_challenge_status"] = challenged.status_code
                                    report["preflight_challenge_header"] = challenged.headers["www-authenticate"]
                                    child_context = {**context, "engine_token": expired.token}
                                async with httpx.AsyncClient(timeout=90, follow_redirects=False) as provider:
                                    response = await provider.post("http://bifrost-pyodide-ddf316c1:8799",
                                        extensions={"trace": trace_provider} if transport_waiter else {},
                                        json=json.loads(json.dumps({"mode": "canonical", "worker_id": str(worker.id),
                                            "execution_id": execution_id, "context": child_context, "manifest": manifest,
                                            **({"fixture_mode": "refresh_interruption"} if interrupt_refresh else {})}, default=str)))
                                assert response.status_code == 200, f"Provider status {response.status_code}"
                                if isolation_label is not None:
                                    report["provider_end_ms"] = int(time.time() * 1000)
                                self.evidence = response.json()
                                encoded = json.dumps(self.evidence)
                                if any(secret and secret in encoded for secret in secrets):
                                    raise RuntimeError("Credential exposure: provider evidence withheld")
                                result = self.evidence["result"]
                                if refresh_probe or expired_access or concurrent_refresh or interrupt_refresh or failure_probe:
                                    document = await api.get(f"/api/tables/{table}/documents/proof", headers=headers,
                                                             params={"scope": str(org.id)})
                                    diagnostic = {**report,
                                        "provider_evidence": self.evidence,
                                        "workflow_document_status": document.status_code}
                                    encoded = json.dumps(diagnostic)
                                    if any(secret and secret in encoded for secret in secrets):
                                        raise RuntimeError("Credential exposure: diagnostic withheld")
                                    print(encoded)
                                    if result["success"] is True and not failure_probe:
                                        assert any(call["path"] == "/auth/refresh" and call["status"] == 200
                                                   for call in self.evidence["api_calls"])
                                        if concurrent_refresh:
                                            assert result["result"]["refresh_callers"] == 3
                                            assert result["result"]["same_replacement"] is True
                                            assert sum(call["path"] == "/auth/refresh"
                                                       for call in self.evidence["api_calls"]) == 1
                                assert result["success"] is expected_success
                                if failure_probe:
                                    assert result["error_type"] == "ValueError"
                                    assert "fixture failure credential=[REDACTED]" in result["error"]
                                    messages = [log["message"] for log in result["logs"]]
                                    report["stdout_capture_verified"] = any("stdout credential=[REDACTED]" in message for message in messages)
                                    assert any("structured credential=[REDACTED]" in message for message in messages)
                                    report["structured_log_exception_redaction_verified"] = True
                                else:
                                    assert result["result"]["value"] == expected_value
                                if integration_probe:
                                    assert result["result"]["integration_value"] == 42
                                    assert result["result"]["integration_peer_status"] == 403
                                    report["integration_read_verified"] = True
                                if integration_secret_probe:
                                    assert result["result"]["integration_secret_probe"] == "[REDACTED]"
                                    assert any(log["message"] == "integration secret=[REDACTED]" for log in result["logs"])
                                    report["integration_secret_decryption_and_redaction_verified"] = True
                                if isolation_label is not None:
                                    value = result["result"]
                                    assert value["tenant_label"] == isolation_label and value["peer_sdk_status"] == 403
                                    assert all(value[key] is None for key in (
                                        "builtins_before", "environment_before", "module_before", "tmp_before"))
                                if expected_success:
                                    assert result["result"]["parent_secret_present"] is False
                                assert result["metrics"]["code_hash"] == manifest[path]
                                document = await api.get(f"/api/tables/{table}/documents/proof", headers=headers,
                                                         params={"scope": str(org.id)})
                                if expected_success:
                                    assert document.status_code == 200 and document.json()["data"]["value"] == expected_value
                                    report["independent_document_value"] = expected_value
                                else:
                                    assert document.status_code == 404
                                    report["independent_document_absent"] = True
                                report["cross_tenant_status"] = denied.status_code
                                if isolation_label is not None:
                                    await rendezvous("effects_done", {"done": True})
                            return result

                    task = await journal.dispatch(ref.lease, "route", execution_id, launch)
                    duplicate = await journal.dispatch(ref.lease, "route", execution_id, launch)
                    assert task is duplicate
                    # Upstream awaits routing acceptance, not the workflow result.
                    # Retain collection independently of its setup error handler.
                    self.receipt = task

            pool = LocalPool()
            pool.journal = journal  # Trusted-parent transport, never supplied to the child.
            consumer = WorkflowExecutionConsumer(pool=pool)
            consumer.owner_worker_id = worker.id
            try:
                with remote_owner_context(ref):
                    claim = await claim_next(worker.id, fits_bytes=2**30, budget_bytes=2**30)
                    assert claim is not None and claim.subject_id == execution.id and claim.claimed is not None
                    liveness.update(active_runs=1, memory_reserved_bytes=claim.memory_request_bytes)
                    await beat()
                    if isolation_label is None:
                        duplicate_claim = await claim_next(worker.id, fits_bytes=2**30, budget_bytes=2**30)
                        assert duplicate_claim is None
                    await consumer.process_message(claim.message, PendingExecution(**claim.claimed))
                report["routing_returned_before_collection_wait"] = True
                if transport_waiter:
                    async def wait_for_provider():
                        return await pool.receipt

                    waiter = asyncio.create_task(wait_for_provider())
                    sent = asyncio.create_task(pool.provider_sent.wait())
                    try:
                        await asyncio.wait((sent, waiter), return_when=asyncio.FIRST_COMPLETED)
                        if waiter.done():
                            await waiter
                            raise RuntimeError("Provider completed before interruption checkpoint")
                        assert pool.provider_sent.is_set()
                        waiter.cancel()
                        with suppress(asyncio.CancelledError):
                            await waiter
                        assert waiter.cancelled()
                        assert journal.inventory()[0]["collection_state"] == "pending"
                        report["transport_waiter_probe"] = True
                        report["provider_request_body_sent"] = True
                        report["cancelled_waiter_collection_retained"] = True
                    finally:
                        sent.cancel()
                        with suppress(asyncio.CancelledError):
                            await sent
                task_result = await pool.receipt
                assert task_result["success"] is expected_success
                assert pool.evidence is not None and pool.info is not None
                original = consumer._handle_result

                async def lose_ack(result):
                    await original(result)
                    raise ConnectionError("Injected loss after canonical completion")

                consumer._handle_result = lose_ack
                try:
                    await journal.reconcile_workflow(ref.lease, "route", consumer, pool.info)
                except ConnectionError as exc:
                    assert str(exc) == "Injected loss after canonical completion"
                else:
                    raise AssertionError("Lost-ACK injection was not observed")
                assert not journal.inventory()[0]["acknowledged"]
                liveness.update(active_runs=0, runs_handled=1, memory_reserved_bytes=0)
                async with get_db_context() as db:
                    row = await db.get(Execution, execution.id)
                    expected_status = ExecutionStatus.SUCCESS if expected_success else ExecutionStatus.FAILED
                    assert row is not None and row.status == expected_status
                    if expected_success:
                        assert row.result["value"] == expected_value
                    else:
                        assert row.result is None and "fixture failure credential=[REDACTED]" in row.error_message
                        report["canonical_failure_error"] = row.error_message
                        report["canonical_failure_status"] = row.status.value
                    logs_before = list(await db.scalars(select(ExecutionLog.id).where(ExecutionLog.execution_id == execution.id)))
                    assert logs_before
                ref.lease = await connect(2)
                await beat()
                consumer._handle_result = original
                reconciled = await journal.reconcile_workflow(ref.lease, "route", consumer, pool.info)
                assert reconciled
                async with get_db_context() as db:
                    logs_after = list(await db.scalars(select(ExecutionLog.id).where(ExecutionLog.execution_id == execution.id)))
                    assert logs_after == logs_before
                    assert await db.scalar(select(ExecutionWorkerClaim).where(
                        ExecutionWorkerClaim.worker_id == worker.id, ExecutionWorkerClaim.subject_id == execution.id))
                async with httpx.AsyncClient(base_url="http://api:8000", timeout=15) as api:
                    revoked = await api.post("/api/sdk/config/get", headers={"Authorization": f"Bearer {pool.token}"},
                                             json={"key": key})
                    assert revoked.status_code == 401
                    if refresh_probe or expired_access or concurrent_refresh or interrupt_refresh or failure_probe:
                        refresh_revoked = await api.post("/auth/refresh",
                            json={"refresh_token": pool.refresh_token})
                        assert refresh_revoked.status_code == 401
                        report["refresh_revoked_after_terminal_status"] = refresh_revoked.status_code
                await journal.retire()
                assert pool.launches == 1
                if heartbeat.done():
                    heartbeat.result()
                report.update({"canonical_claim_verified": True, "canonical_terminal_consumption_verified": True,
                    "lost_ack_reconnect_verified": True, "credential_revoked_after_terminal_status": revoked.status_code,
                    "launches": pool.launches, "persisted_log_count": len(logs_before), "result": pool.evidence,
                    "completed_claim_retained_as_upstream_specifies": True})
            finally:
                if heartbeat is not None:
                    heartbeat.cancel()
                    with suppress(asyncio.CancelledError):
                        await heartbeat
                if ref is not None:
                    await release_remote_owner(ref.lease)
                async with get_db_context() as db:
                    await db.execute(delete(ExecutionLog).where(ExecutionLog.execution_id == execution.id))
                    await db.execute(delete(WorkDispatch).where(WorkDispatch.subject_id == execution.id))
        async with get_db_context() as db:
            assert await db.scalar(select(ExecutionWorkerClaim).where(ExecutionWorkerClaim.worker_id == worker.id)) is None
        report["fixture_worker_cascade_cleanup_verified"] = True
        encoded = json.dumps(report)
        if any(secret and secret in encoded for secret in secrets):
            raise RuntimeError("Credential exposure: evidence withheld")
        print(encoded)
        if failure_probe:
            assert report["stdout_capture_verified"], "Upstream stdout capture remains unqualified"
    except Exception:
        if isolation_label is not None:
            with suppress(Exception):
                async with get_redis() as redis:
                    await redis.set(f"bifrost:pyodide-isolation:{batch}:abort:{isolation_label}", "failed", ex=90)
        raise
    finally:
        if heartbeat is not None and not heartbeat.done():
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        for name in sources:
            await invalidate_module(name)
            await storage.delete(name)
        await close_db()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    try:
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--isolation-label", choices=("tenant-a", "tenant-b"))
        parser.add_argument("--batch")
        parser.add_argument("--refresh-probe", action="store_true")
        parser.add_argument("--expired-access", action="store_true")
        parser.add_argument("--concurrent-refresh", action="store_true")
        parser.add_argument("--interrupt-refresh", action="store_true")
        parser.add_argument("--failure-probe", action="store_true")
        parser.add_argument("--transport-waiter", action="store_true")
        parser.add_argument("--node-pyodide", action="store_true")
        parser.add_argument("--integration-probe", action="store_true")
        parser.add_argument("--integration-secret-probe", action="store_true")
        parser.add_argument("--benchmark-probe", action="store_true")
        args = parser.parse_args()
        asyncio.run(main(benchmark_probe=args.benchmark_probe, isolation_label=args.isolation_label, batch=args.batch,
                         refresh_probe=args.refresh_probe, expired_access=args.expired_access,
                         concurrent_refresh=args.concurrent_refresh, interrupt_refresh=args.interrupt_refresh,
                         failure_probe=args.failure_probe, transport_waiter=args.transport_waiter,
                         node_pyodide=args.node_pyodide, integration_probe=args.integration_probe,
                         integration_secret_probe=args.integration_secret_probe))
    except Exception as exc:
        import traceback

        chain = []
        pending_errors = [exc]
        seen = set()
        while pending_errors and len(chain) < 12:
            error = pending_errors.pop(0)
            if id(error) in seen:
                continue
            seen.add(id(error))
            chain.append({"error_type": type(error).__name__,
                "constraint": getattr(error, "constraint_name", None),
                "table": getattr(error, "table_name", None),
                "locations": [f"{Path(frame.filename).name}:{frame.lineno}"
                              for frame in traceback.extract_tb(error.__traceback__)]})
            pending_errors.extend(e for e in (error.__cause__, error.__context__) if e is not None)
        print(json.dumps({"runtime": "local-workerd-canonical-claim-consumer",
            "error_type": type(exc).__name__,
            "exception_chain": chain,
            "locations": [f"{Path(frame.filename).name}:{frame.lineno}"
                          for frame in traceback.extract_tb(exc.__traceback__)]}))
        raise SystemExit(1)
